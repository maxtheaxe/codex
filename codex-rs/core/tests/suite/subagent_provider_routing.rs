//! Exercises native subagents across independently authenticated Responses providers.

use anyhow::Result;
use codex_core::config::Config;
use codex_features::Feature;
use codex_login::CodexAuth;
use codex_protocol::protocol::EventMsg;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed;
use core_test_support::responses::ev_function_call_with_namespace;
use core_test_support::responses::ev_response_created;
use core_test_support::responses::mount_sse_once_match;
use core_test_support::responses::sse;
use core_test_support::responses::start_mock_server;
use core_test_support::test_codex::test_codex;
use core_test_support::wait_for_event;
use pretty_assertions::assert_eq;
use serde_json::json;
use test_case::test_case;

const ROOT_PROMPT: &str = "start the provider routing worker";
const CHILD_PROMPT: &str = "do the provider routing work";
const ROOT_MODEL: &str = "gpt-5.5";
const CHILD_MODEL: &str = "gpt-5.6-sol";

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
    let mut builder = test_codex()
        .with_auth(CodexAuth::from_api_key("openai-test-key"))
        .with_model(ROOT_MODEL)
        .with_pre_build_hook(move |home| {
            std::fs::write(home.join("config.toml"), format!(
                "[model_providers.openrouter]\nname = 'OpenRouter'\nbase_url = '{openrouter_url}/v1'\nexperimental_bearer_token = 'openrouter-test-key'\nwire_api = 'responses'\nrequest_max_retries = 0\nstream_max_retries = 0\n"
            )).unwrap();
        })
        .with_config(move |config| configure(config, &root_provider_owned, &openai_url));
    let test = builder.build_with_auto_env(root_server).await?;
    let mut created_threads = test.thread_manager.subscribe_thread_created();

    let mut args = json!({"message": CHILD_PROMPT, "task_name": "worker", "model": CHILD_MODEL, "fork_turns": "none"});
    if let Some(provider) = selected {
        args["model_provider"] = json!(provider);
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

    test.submit_text_turn(ROOT_PROMPT).await?;
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
    let root = root_request.single_request();
    let child_http = child_request.single_request();
    assert_eq!(root.body_json()["model"], ROOT_MODEL);
    assert_eq!(child_http.body_json()["model"], CHILD_MODEL);
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
            .single_request()
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
    test.submit_text_turn("inspect and message the routing worker")
        .await?;
    let list_output = list_completion.single_request();
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
    test.submit_text_turn("continue the routing worker").await?;
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        wait_for_event(child.as_ref(), |event| {
            matches!(event, EventMsg::TurnComplete(_))
        }),
    )
    .await?;
    assert_eq!(
        followup_request
            .single_request()
            .header("authorization")
            .as_deref(),
        Some(expected_auth(root_provider))
    );
    let continued = child_followup.single_request();
    assert_eq!(continued.body_json()["model"], CHILD_MODEL);
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
            .single_request()
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
    test.submit_text_turn("interrupt the routing worker")
        .await?;
    let interrupt: serde_json::Value = serde_json::from_str(
        &interrupt_completion
            .single_request()
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
    test.submit_text_turn("try missing provider").await?;
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
