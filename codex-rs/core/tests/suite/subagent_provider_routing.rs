//! Exercises native subagents across independently authenticated Responses providers.

use anyhow::Result;
use codex_core::config::Config;
use codex_features::Feature;
use codex_login::CodexAuth;
use codex_protocol::protocol::EventMsg;
use core_test_support::responses::ResponseMock;
use core_test_support::responses::ResponsesRequest;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed;
use core_test_support::responses::ev_function_call_with_namespace;
use core_test_support::responses::ev_response_created;
use core_test_support::responses::mount_response_once_match;
use core_test_support::responses::mount_sse_once_match;
use core_test_support::responses::namespace_child_tool;
use core_test_support::responses::sse;
use core_test_support::responses::sse_response;
use core_test_support::responses::start_mock_server;
use core_test_support::test_codex::TestCodex;
use core_test_support::test_codex::test_codex;
use core_test_support::wait_for_event;
use pretty_assertions::assert_eq;
use serde_json::json;
use test_case::test_case;

const ROOT_PROMPT: &str = "start the provider routing worker";
const CHILD_PROMPT: &str = "do the provider routing work";
const ROOT_MODEL: &str = "gpt-5.5";
const CHILD_MODEL: &str = "gpt-5.6-sol";
const CUSTOM_ROOT_MODEL: &str = "vendor/parent-model";
const CUSTOM_CHILD_MODEL: &str = "vendor/test-model";

fn body_contains(request: &wiremock::Request, text: &str) -> bool {
    let body = match request
        .headers
        .get("content-encoding")
        .and_then(|v| v.to_str().ok())
    {
        Some(value) if value.eq_ignore_ascii_case("zstd") => {
            zstd::stream::decode_all(std::io::Cursor::new(&request.body)).ok()
        }
        _ => Some(request.body.clone()),
    };
    body.and_then(|body| String::from_utf8(body).ok())
        .is_some_and(|body| body.contains(text))
}

fn captured_request(
    mock: &ResponseMock,
    required: &str,
    forbidden: Option<&str>,
) -> ResponsesRequest {
    mock.requests()
        .into_iter()
        .find(|request| {
            let body = request.body_json().to_string();
            body.contains(required) && forbidden.is_none_or(|text| !body.contains(text))
        })
        .expect("expected matching recorded request")
}

async fn submit(test: &TestCodex, prompt: &str) -> Result<()> {
    tokio::time::timeout(
        std::time::Duration::from_secs(30),
        test.submit_text_turn(prompt),
    )
    .await??;
    Ok(())
}

fn configure(config: &mut Config, root_provider: &str, openai_url: &str) {
    config.features.enable(Feature::Collab).unwrap();
    config.features.enable(Feature::MultiAgentV2).unwrap();
    let openai = config.model_providers.get_mut("openai").unwrap();
    openai.base_url = Some(format!("{openai_url}/v1"));
    openai.supports_websockets = false;
    openai.request_max_retries = Some(0);
    openai.stream_max_retries = Some(0);
    config.model_provider_id = root_provider.to_string();
    config.model_provider = config.model_providers[root_provider].clone();
}

#[test_case("openai", Some("openrouter"); "openai_to_openrouter")]
#[test_case("openrouter", Some("openai"); "openrouter_to_openai")]
#[test_case("openai", None; "omitted_provider_inherits_openai")]
#[test_case("openrouter", None; "omitted_provider_inherits_openrouter")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn spawn_routes_child_to_selected_provider(
    root_provider: &str,
    selected: Option<&str>,
) -> Result<()> {
    let openai = start_mock_server().await;
    let openrouter = start_mock_server().await;
    let root_server = if root_provider == "openai" {
        &openai
    } else {
        &openrouter
    };
    let child_provider = selected.unwrap_or(root_provider);
    let child_server = if child_provider == "openai" {
        &openai
    } else {
        &openrouter
    };
    let openrouter_url = openrouter.uri();
    let openai_url = openai.uri();
    let root_provider_owned = root_provider.to_string();
    let root_model = if root_provider == "openai" {
        ROOT_MODEL
    } else {
        CUSTOM_ROOT_MODEL
    };
    let child_model = match selected {
        Some("openai") => CHILD_MODEL,
        Some(_) => CUSTOM_CHILD_MODEL,
        None => root_model,
    };
    let mut builder = test_codex()
        .with_auth(CodexAuth::from_api_key("openai-test-key"))
        .with_model(root_model)
        .with_pre_build_hook(move |home| {
            std::fs::write(home.join("config.toml"), format!(
                "[model_providers.openrouter]\nname = 'OpenRouter'\nbase_url = '{openrouter_url}/v1'\nexperimental_bearer_token = 'openrouter-test-key'\nwire_api = 'responses'\nrequest_max_retries = 0\nstream_max_retries = 0\n"
            )).unwrap();
        })
        .with_config(move |config| {
            configure(config, &root_provider_owned, &openai_url);
            if root_provider_owned == "openai" {
                config.service_tier = Some("flex".to_string());
            }
        });
    let test = builder.build_with_auto_env(root_server).await?;
    let mut created_threads = test.thread_manager.subscribe_thread_created();

    let mut args = json!({"message": CHILD_PROMPT, "task_name": "worker", "fork_turns": "none"});
    if let Some(provider) = selected {
        args["model_provider"] = json!(provider);
        args["model"] = json!(child_model);
    }
    let root_request = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| {
            body_contains(request, ROOT_PROMPT) && !body_contains(request, "spawn-provider-worker")
        },
        sse(vec![
            ev_response_created("root-spawn"),
            ev_function_call_with_namespace(
                "spawn-provider-worker",
                "collaboration",
                "spawn_agent",
                &args.to_string(),
            ),
            ev_completed("root-spawn"),
        ]),
    )
    .await;
    let child_request = mount_sse_once_match(
        child_server,
        |request: &wiremock::Request| {
            body_contains(request, CHILD_PROMPT) && !body_contains(request, ROOT_PROMPT)
        },
        sse(vec![
            ev_response_created("child"),
            ev_assistant_message("child-answer", "provider worker finished"),
            ev_completed("child"),
        ]),
    )
    .await;
    let root_completion = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| {
            body_contains(request, "spawn-provider-worker") && body_contains(request, ROOT_PROMPT)
        },
        sse(vec![
            ev_response_created("root-done"),
            ev_assistant_message("root-answer", "worker started"),
            ev_completed("root-done"),
        ]),
    )
    .await;

    submit(&test, ROOT_PROMPT).await?;
    let child_id =
        tokio::time::timeout(std::time::Duration::from_secs(10), created_threads.recv()).await??;
    let child = test.thread_manager.get_thread(child_id).await?;
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        wait_for_event(child.as_ref(), |event| {
            matches!(event, EventMsg::TurnComplete(_))
        }),
    )
    .await?;
    let root = captured_request(&root_request, ROOT_PROMPT, Some("spawn-provider-worker"));
    let child_http = captured_request(&child_request, CHILD_PROMPT, Some(ROOT_PROMPT));
    assert_eq!(root.body_json()["model"], root_model);
    assert!(
        namespace_child_tool(&root.body_json(), "collaboration", "spawn_agent")
            .and_then(|tool| tool.pointer("/parameters/properties/model_provider"))
            .is_some()
    );
    assert_eq!(child_http.body_json()["model"], child_model);
    if root_provider == "openai" {
        assert_eq!(root.body_json()["service_tier"], "flex");
        let expected_child_tier = if selected.is_none() {
            json!("flex")
        } else {
            serde_json::Value::Null
        };
        assert_eq!(child_http.body_json()["service_tier"], expected_child_tier);
    }
    let expected_auth = |provider| {
        if provider == "openai" {
            "Bearer openai-test-key"
        } else {
            "Bearer openrouter-test-key"
        }
    };
    assert_eq!(
        root.header("authorization").as_deref(),
        Some(expected_auth(root_provider))
    );
    assert_eq!(
        child_http.header("authorization").as_deref(),
        Some(expected_auth(child_provider))
    );
    assert!(
        root_completion
            .function_call_output_text("spawn-provider-worker")
            .is_some()
    );
    // Queue a native message and inspect the child through the native agent list.
    mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| {
            body_contains(request, "inspect and message the routing worker")
                && !body_contains(request, "list-provider-worker")
        },
        sse(vec![
            ev_response_created("root-list"),
            ev_function_call_with_namespace(
                "list-provider-worker",
                "collaboration",
                "list_agents",
                "{}",
            ),
            ev_function_call_with_namespace(
                "message-provider-worker",
                "collaboration",
                "send_message",
                &json!({"target": "worker", "message": "queued provider context"}).to_string(),
            ),
            ev_completed("root-list"),
        ]),
    )
    .await;
    let list_completion = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| body_contains(request, "list-provider-worker"),
        sse(vec![
            ev_response_created("root-list-done"),
            ev_assistant_message("root-list-answer", "worker listed and messaged"),
            ev_completed("root-list-done"),
        ]),
    )
    .await;
    submit(&test, "inspect and message the routing worker").await?;
    let list_output = captured_request(&list_completion, "list-provider-worker", None);
    let list: serde_json::Value = serde_json::from_str(
        &list_output
            .function_call_output_text("list-provider-worker")
            .expect("list output"),
    )?;
    let worker = list["agents"]
        .as_array()
        .expect("agents")
        .iter()
        .find(|agent| agent["agent_name"] == "/root/worker")
        .expect("worker in list");
    assert_eq!(
        worker["agent_status"],
        json!({"completed": "provider worker finished"})
    );
    assert_eq!(
        list_output
            .function_call_output_text("message-provider-worker")
            .as_deref(),
        Some("")
    );

    // A later native follow-up must use the child's selected provider too.
    let followup_request = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| {
            body_contains(request, "continue the routing worker")
                && !body_contains(request, "followup-provider-worker")
        },
        sse(vec![
            ev_response_created("root-followup"),
            ev_function_call_with_namespace(
                "followup-provider-worker",
                "collaboration",
                "followup_task",
                &json!({"target": "worker", "message": "finish the routing followup"}).to_string(),
            ),
            ev_completed("root-followup"),
        ]),
    )
    .await;
    let child_followup = mount_sse_once_match(
        child_server,
        |request: &wiremock::Request| {
            body_contains(request, "finish the routing followup")
                && !body_contains(request, "continue the routing worker")
        },
        sse(vec![
            ev_response_created("child-followup"),
            ev_assistant_message("child-followup-answer", "followup finished"),
            ev_completed("child-followup"),
        ]),
    )
    .await;
    let followup_completion = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| body_contains(request, "followup-provider-worker"),
        sse(vec![
            ev_response_created("root-followup-done"),
            ev_assistant_message("root-followup-answer", "followup sent"),
            ev_completed("root-followup-done"),
        ]),
    )
    .await;
    submit(&test, "continue the routing worker").await?;
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        wait_for_event(child.as_ref(), |event| {
            matches!(event, EventMsg::TurnComplete(_))
        }),
    )
    .await?;
    assert_eq!(
        captured_request(
            &followup_request,
            "continue the routing worker",
            Some("followup-provider-worker")
        )
        .header("authorization")
        .as_deref(),
        Some(expected_auth(root_provider))
    );
    let continued = captured_request(
        &child_followup,
        "finish the routing followup",
        Some("continue the routing worker"),
    );
    assert_eq!(continued.body_json()["model"], child_model);
    if root_provider == "openai" {
        let expected_child_tier = if selected.is_none() {
            json!("flex")
        } else {
            serde_json::Value::Null
        };
        assert_eq!(continued.body_json()["service_tier"], expected_child_tier);
    }
    assert!(
        continued
            .body_json()
            .to_string()
            .contains("queued provider context")
    );
    assert_eq!(
        continued.header("authorization").as_deref(),
        Some(expected_auth(child_provider))
    );
    assert_eq!(
        followup_completion
            .function_call_output_text("followup-provider-worker")
            .as_deref(),
        Some("")
    );
    mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| {
            body_contains(request, "interrupt the routing worker")
                && !body_contains(request, "interrupt-provider-worker")
        },
        sse(vec![
            ev_response_created("root-interrupt"),
            ev_function_call_with_namespace(
                "interrupt-provider-worker",
                "collaboration",
                "interrupt_agent",
                &json!({"target": "worker"}).to_string(),
            ),
            ev_completed("root-interrupt"),
        ]),
    )
    .await;
    let interrupt_completion = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| body_contains(request, "interrupt-provider-worker"),
        sse(vec![
            ev_response_created("root-interrupt-done"),
            ev_assistant_message("root-interrupt-answer", "worker interrupted"),
            ev_completed("root-interrupt-done"),
        ]),
    )
    .await;
    submit(&test, "interrupt the routing worker").await?;
    let interrupt: serde_json::Value = serde_json::from_str(
        &interrupt_completion
            .function_call_output_text("interrupt-provider-worker")
            .expect("interrupt output"),
    )?;
    assert_eq!(
        interrupt["previous_status"],
        json!({"completed": "followup finished"})
    );
    test.codex.shutdown_and_wait().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn unknown_provider_is_reported_to_the_calling_agent() -> Result<()> {
    let server = start_mock_server().await;
    let mut builder = test_codex().with_config(|config| {
        config.features.enable(Feature::Collab).unwrap();
        config.features.enable(Feature::MultiAgentV2).unwrap();
    });
    let test = builder.build_with_auto_env(&server).await?;
    let mut created_threads = test.thread_manager.subscribe_thread_created();
    mount_sse_once_match(&server,
        |request: &wiremock::Request| body_contains(request, "try missing provider") && !body_contains(request, "bad-provider"),
        sse(vec![ev_response_created("bad-spawn"), ev_function_call_with_namespace("bad-provider", "collaboration", "spawn_agent", &json!({"task_name": "worker", "message": "work", "model": CHILD_MODEL, "model_provider": "provider-that-does-not-exist", "fork_turns": "none"}).to_string()), ev_completed("bad-spawn")])).await;
    let completion = mount_sse_once_match(
        &server,
        |request: &wiremock::Request| body_contains(request, "bad-provider"),
        sse(vec![
            ev_response_created("bad-spawn-done"),
            ev_assistant_message("bad-spawn-answer", "provider unavailable"),
            ev_completed("bad-spawn-done"),
        ]),
    )
    .await;
    submit(&test, "try missing provider").await?;
    let output = completion
        .single_request()
        .function_call_output_text("bad-provider")
        .expect("spawn error output");
    assert!(output.contains("provider-that-does-not-exist"), "{output}");
    assert!(output.to_lowercase().contains("provider"), "{output}");
    assert!(
        created_threads.try_recv().is_err(),
        "invalid provider must not spawn a child"
    );
    test.codex.shutdown_and_wait().await?;
    Ok(())
}

#[test_case("openai", "openrouter"; "openai_to_openrouter")]
#[test_case("openrouter", "openai"; "openrouter_to_openai")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn legacy_spawn_routes_to_selected_provider(
    root_provider: &str,
    child_provider: &str,
) -> Result<()> {
    let openai = start_mock_server().await;
    let openrouter = start_mock_server().await;
    let root_server = if root_provider == "openai" {
        &openai
    } else {
        &openrouter
    };
    let child_server = if child_provider == "openai" {
        &openai
    } else {
        &openrouter
    };
    let root_model = if root_provider == "openai" {
        ROOT_MODEL
    } else {
        CUSTOM_ROOT_MODEL
    };
    let child_model = if child_provider == "openai" {
        CHILD_MODEL
    } else {
        CUSTOM_CHILD_MODEL
    };
    let root_provider_owned = root_provider.to_string();
    let openai_url = openai.uri();
    let openrouter_url = openrouter.uri();
    let mut builder = test_codex()
        .with_auth(CodexAuth::from_api_key("openai-test-key"))
        .with_model(root_model)
        .with_pre_build_hook(move |home| {
            std::fs::write(home.join("config.toml"), format!(
                "[model_providers.openrouter]\nname = 'OpenRouter'\nbase_url = '{openrouter_url}/v1'\nexperimental_bearer_token = 'openrouter-test-key'\nwire_api = 'responses'\nrequest_max_retries = 0\nstream_max_retries = 0\n"
            )).unwrap();
        })
        .with_config(move |config| {
            configure(config, &root_provider_owned, &openai_url);
            config.features.disable(Feature::MultiAgentV2).unwrap();
        });
    let test = builder.build_with_auto_env(root_server).await?;
    let mut created = test.thread_manager.subscribe_thread_created();
    let root_request = mount_sse_once_match(root_server,
        |request: &wiremock::Request| body_contains(request, "start legacy provider worker") && !body_contains(request, "legacy-provider-spawn"),
        sse(vec![ev_response_created("legacy-spawn"), ev_function_call_with_namespace("legacy-provider-spawn", "multi_agent_v1", "spawn_agent", &json!({"message": "legacy provider work", "model_provider": child_provider, "model": child_model, "fork_context": false}).to_string()), ev_completed("legacy-spawn")])).await;
    let child_request = mount_sse_once_match(
        child_server,
        |request: &wiremock::Request| {
            body_contains(request, "legacy provider work")
                && !body_contains(request, "start legacy provider worker")
        },
        sse(vec![
            ev_response_created("legacy-child"),
            ev_assistant_message("legacy-child-answer", "legacy child finished"),
            ev_completed("legacy-child"),
        ]),
    )
    .await;
    let root_completion = mount_sse_once_match(
        root_server,
        |request: &wiremock::Request| body_contains(request, "legacy-provider-spawn"),
        sse(vec![
            ev_response_created("legacy-done"),
            ev_assistant_message("legacy-answer", "legacy worker started"),
            ev_completed("legacy-done"),
        ]),
    )
    .await;
    submit(&test, "start legacy provider worker").await?;
    let child_id =
        tokio::time::timeout(std::time::Duration::from_secs(10), created.recv()).await??;
    let child = test.thread_manager.get_thread(child_id).await?;
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        wait_for_event(child.as_ref(), |event| {
            matches!(event, EventMsg::TurnComplete(_))
        }),
    )
    .await?;
    let expected_auth = |provider| {
        if provider == "openai" {
            "Bearer openai-test-key"
        } else {
            "Bearer openrouter-test-key"
        }
    };
    let root = root_request.single_request();
    let child_http = child_request.single_request();
    assert_eq!(root.body_json()["model"], root_model);
    // OpenAI models may expose this namespace through deferred tool search.
    if let Some(tool) = namespace_child_tool(&root.body_json(), "multi_agent_v1", "spawn_agent") {
        assert!(
            tool.pointer("/parameters/properties/model_provider")
                .is_some()
        );
    }
    assert_eq!(child_http.body_json()["model"], child_model);
    assert_eq!(
        root.header("authorization").as_deref(),
        Some(expected_auth(root_provider))
    );
    assert_eq!(
        child_http.header("authorization").as_deref(),
        Some(expected_auth(child_provider))
    );
    let spawn_output: serde_json::Value = serde_json::from_str(
        &root_completion
            .single_request()
            .function_call_output_text("legacy-provider-spawn")
            .expect("spawn output"),
    )?;
    assert_eq!(spawn_output["agent_id"], child_id.to_string());
    child.shutdown_and_wait().await?;
    test.codex.shutdown_and_wait().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn switched_child_can_be_interrupted_while_active_and_continued() -> Result<()> {
    let openai = start_mock_server().await;
    let openrouter = start_mock_server().await;
    let openai_url = openai.uri();
    let openrouter_url = openrouter.uri();
    let mut builder = test_codex()
        .with_auth(CodexAuth::from_api_key("openai-test-key"))
        .with_model(ROOT_MODEL)
        .with_pre_build_hook(move |home| {
            std::fs::write(home.join("config.toml"), format!(
                "[model_providers.openrouter]\nname = 'OpenRouter'\nbase_url = '{openrouter_url}/v1'\nexperimental_bearer_token = 'openrouter-test-key'\nwire_api = 'responses'\nrequest_max_retries = 0\nstream_max_retries = 0\n"
            )).unwrap();
        })
        .with_config(move |config| configure(config, "openai", &openai_url));
    let test = builder.build_with_auto_env(&openai).await?;
    let mut created = test.thread_manager.subscribe_thread_created();
    mount_sse_once_match(&openai,
        |request: &wiremock::Request| body_contains(request, "start active routing worker") && !body_contains(request, "active-spawn"),
        sse(vec![ev_response_created("active-root"), ev_function_call_with_namespace("active-spawn", "collaboration", "spawn_agent", &json!({"message": "pause active routing worker", "task_name": "worker", "model_provider": "openrouter", "model": CUSTOM_CHILD_MODEL, "fork_turns": "none"}).to_string()), ev_completed("active-root")])).await;
    mount_sse_once_match(
        &openai,
        |request: &wiremock::Request| {
            body_contains(request, "active-spawn") && !body_contains(request, "active-interrupt")
        },
        sse(vec![
            ev_response_created("active-root-done"),
            ev_assistant_message("active-root-answer", "worker started"),
            ev_completed("active-root-done"),
        ]),
    )
    .await;
    // Keep the model request in flight until the parent interrupts the child.
    let first_child = mount_response_once_match(
        &openrouter,
        |request: &wiremock::Request| body_contains(request, "pause active routing worker"),
        sse_response(sse(vec![
            ev_response_created("active-child"),
            ev_assistant_message("active-child-answer", "should be interrupted"),
            ev_completed("active-child"),
        ]))
        .set_delay(std::time::Duration::from_secs(30)),
    )
    .await;
    submit(&test, "start active routing worker").await?;
    let child_id =
        tokio::time::timeout(std::time::Duration::from_secs(10), created.recv()).await??;
    let child = test.thread_manager.get_thread(child_id).await?;
    tokio::time::timeout(std::time::Duration::from_secs(10), async {
        while first_child.requests().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await?;
    assert_eq!(
        first_child
            .single_request()
            .header("authorization")
            .as_deref(),
        Some("Bearer openrouter-test-key")
    );
    mount_sse_once_match(
        &openai,
        |request: &wiremock::Request| {
            body_contains(request, "stop active routing worker")
                && !body_contains(request, "active-interrupt")
        },
        sse(vec![
            ev_response_created("stop-root"),
            ev_function_call_with_namespace(
                "active-interrupt",
                "collaboration",
                "interrupt_agent",
                &json!({"target": "worker"}).to_string(),
            ),
            ev_completed("stop-root"),
        ]),
    )
    .await;
    let interrupted = mount_sse_once_match(
        &openai,
        |request: &wiremock::Request| body_contains(request, "active-interrupt"),
        sse(vec![
            ev_response_created("stop-root-done"),
            ev_assistant_message("stop-root-answer", "worker stopped"),
            ev_completed("stop-root-done"),
        ]),
    )
    .await;
    submit(&test, "stop active routing worker").await?;
    let interrupt: serde_json::Value = serde_json::from_str(
        &interrupted
            .single_request()
            .function_call_output_text("active-interrupt")
            .expect("interrupt output"),
    )?;
    assert_eq!(interrupt["previous_status"], "running");
    mount_sse_once_match(
        &openai,
        |request: &wiremock::Request| {
            body_contains(request, "resume active routing worker")
                && !body_contains(request, "active-resume")
        },
        sse(vec![
            ev_response_created("resume-root"),
            ev_function_call_with_namespace(
                "active-resume",
                "collaboration",
                "followup_task",
                &json!({"target": "worker", "message": "finish after interruption"}).to_string(),
            ),
            ev_completed("resume-root"),
        ]),
    )
    .await;
    let resume_result = mount_sse_once_match(
        &openai,
        |request: &wiremock::Request| body_contains(request, "active-resume"),
        sse(vec![
            ev_response_created("resume-root-done"),
            ev_assistant_message("resume-root-answer", "worker resumed"),
            ev_completed("resume-root-done"),
        ]),
    )
    .await;
    let resumed_child = mount_sse_once_match(
        &openrouter,
        |request: &wiremock::Request| body_contains(request, "finish after interruption"),
        sse(vec![
            ev_response_created("resume-child"),
            ev_assistant_message("resume-child-answer", "resumed child finished"),
            ev_completed("resume-child"),
        ]),
    )
    .await;
    submit(&test, "resume active routing worker").await?;
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        wait_for_event(child.as_ref(), |event| {
            matches!(event, EventMsg::TurnComplete(_))
        }),
    )
    .await?;
    assert_eq!(
        resume_result
            .single_request()
            .function_call_output_text("active-resume")
            .as_deref(),
        Some("")
    );
    let resumed = resumed_child.single_request();
    assert_eq!(resumed.body_json()["model"], CUSTOM_CHILD_MODEL);
    assert_eq!(
        resumed.header("authorization").as_deref(),
        Some("Bearer openrouter-test-key")
    );
    child.shutdown_and_wait().await?;
    test.codex.shutdown_and_wait().await?;
    Ok(())
}
