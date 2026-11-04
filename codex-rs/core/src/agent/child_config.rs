//! Prepares child configuration from captured step settings and requested overrides.
//!
//! Spawn and reload share live runtime policy; role and model precedence, full-history
//! inheritance, and validation messages remain the same for each multi-agent version.

use crate::agent::role::DEFAULT_ROLE_NAME;
use crate::agent::role::apply_role_to_config;
use crate::config::Config;
use crate::session::session::Session;
use crate::session::step_context::StepContext;
use crate::session::turn_context::TurnContext;
use codex_features::Feature;
use codex_models_manager::manager::RefreshStrategy;
use codex_models_manager::manager::SharedModelsManager;
use codex_protocol::config_types::SERVICE_TIER_DEFAULT_REQUEST_VALUE;
use codex_protocol::models::BaseInstructions;
use codex_protocol::models::BaseInstructionsProvenance;
use codex_protocol::openai_models::ModelOrigin;
use codex_protocol::openai_models::ModelPreset;
use codex_protocol::openai_models::ReasoningEffort;
use codex_protocol::openai_models::ReasoningEffortPreset;
use codex_protocol::protocol::MultiAgentVersion;
use std::sync::Arc;

pub(crate) const MAX_SPAWN_AGENT_MODEL_OVERRIDES: usize = 5;

pub(crate) fn model_supports_multi_agent_backend(
    model: &ModelPreset,
    multi_agent_version: MultiAgentVersion,
) -> bool {
    multi_agent_version != MultiAgentVersion::V2
        || model.multi_agent_version != Some(MultiAgentVersion::Disabled)
}

/// Selects the existing spawn tool's role-inheritance rules.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum SpawnConfigVersion {
    V1,
    V2,
}

pub(crate) struct SpawnConfigOptions<'a> {
    pub(crate) version: SpawnConfigVersion,
    pub(crate) full_history_fork: bool,
    pub(crate) role_name: Option<&'a str>,
    pub(crate) model: Option<&'a str>,
    pub(crate) model_provider: Option<&'a str>,
    pub(crate) reasoning_effort: Option<ReasoningEffort>,
}

pub(crate) struct PreparedSpawnConfig {
    pub(crate) config: Config,
    pub(crate) role_name: Option<String>,
}

/// Resolves child settings before starting the thread, retaining the invoking tool's precedence.
pub(crate) async fn prepare_agent_spawn_config(
    session: &Session,
    step_context: &StepContext,
    options: SpawnConfigOptions<'_>,
) -> Result<PreparedSpawnConfig, String> {
    let turn = step_context.turn.as_ref();
    let mut config =
        build_agent_spawn_config(&session.get_base_instructions().await, step_context)?;
    if options.version == SpawnConfigVersion::V1 && options.full_history_fork {
        reject_full_fork_agent_type_override(options.role_name)?;
    }
    let provider_switched = if let Some(provider) = options.model_provider {
        let switched = select_agent_model_provider(&mut config, provider)?;
        if switched && options.full_history_fork {
            return Err("Cross-provider full-history forks are unsupported; specify model_provider and model with fork_turns=none (or fork_context=false).".to_string());
        }
        if switched && options.model.is_none_or(|model| model.trim().is_empty()) {
            return Err(
                "Switching model_provider requires an explicit nonempty model.".to_string(),
            );
        }
        switched
    } else {
        false
    };
    let models_manager =
        spawn_models_manager(session, &config, provider_switched, &turn.originator);
    apply_requested_spawn_agent_model_overrides(
        step_context,
        &mut config,
        options.model,
        options.reasoning_effort,
        &models_manager,
        options.model_provider.is_some(),
        provider_switched,
    )
    .await?;
    let selected_model = config.model.clone();
    let selected_effort = config.model_reasoning_effort.clone();
    if !options.full_history_fork
        || (options.version == SpawnConfigVersion::V2 && options.role_name.is_some())
    {
        if provider_switched {
            apply_role_to_config(&mut config, options.role_name).await?;
        } else {
            apply_spawn_agent_role(&models_manager, &mut config, options.role_name).await?;
        }
        if options.version == SpawnConfigVersion::V2
            && options.full_history_fork
            && config.developer_instructions.is_none()
        {
            config
                .developer_instructions
                .clone_from(&turn.developer_instructions);
        }
    }
    if provider_switched {
        // The explicit cross-provider model must not be replaced by a role configured for the parent.
        config.model = selected_model;
        config.model_reasoning_effort = selected_effort;
        config.service_tier = None;
    } else {
        apply_spawn_agent_service_tier(session, &mut config).await?;
    }
    apply_spawn_agent_runtime_overrides(&mut config, turn)?;

    // Remember an applied configured default so cold reload reapplies its restrictions.
    let role_name = options
        .role_name
        .or_else(|| {
            (options.version == SpawnConfigVersion::V2
                && !options.full_history_fork
                && config
                    .agent_roles
                    .get(DEFAULT_ROLE_NAME)
                    .is_some_and(|role| role.config_file.is_some()))
            .then_some(DEFAULT_ROLE_NAME)
        })
        .map(str::to_owned);
    Ok(PreparedSpawnConfig { config, role_name })
}

/// Select a registered provider while respecting managed policy. Returns whether it changed.
pub(crate) fn select_agent_model_provider(config: &mut Config, id: &str) -> Result<bool, String> {
    let id = id.trim();
    if id.is_empty() {
        return Err("model_provider must be a nonempty configured provider ID.".to_string());
    }
    if let Some(required) = config.config_layer_stack.required_model_provider()
        && required != id
    {
        return Err(format!(
            "model_provider is managed and must be `{required}`."
        ));
    }
    if config.model_provider_id == id {
        return Ok(false);
    }
    let provider = config
        .model_providers
        .get(id)
        .ok_or_else(|| format!("Unknown model_provider `{id}`; use a configured provider ID."))?
        .clone();
    config.model_provider_id = id.to_string();
    config.model_provider = provider;
    config.model_catalog = None;
    config.agent_default_subagent_model = None;
    config.agent_default_subagent_reasoning_effort = None;
    config.model_context_window = None;
    config.model_auto_compact_token_limit = None;
    config.model_reasoning_effort = None;
    config.model_reasoning_summary = None;
    config.model_verbosity = None;
    config.service_tier = None;
    if matches!(
        config.base_instructions_provenance,
        Some(BaseInstructionsProvenance::Model { .. })
    ) {
        config.base_instructions = None;
        config.base_instructions_provenance = None;
    }
    Ok(true)
}

fn spawn_models_manager(
    session: &Session,
    config: &Config,
    provider_switched: bool,
    originator: &str,
) -> SharedModelsManager {
    if !provider_switched {
        return Arc::clone(&session.services.models_manager);
    }
    let credentials = Arc::clone(&session.services.auth_manager);
    if let Some(manager) = session
        .services
        .local_agent_runtime
        .models_manager_for_thread(
            config,
            Arc::clone(&credentials),
            &session.services.runtime_auth,
            originator,
        )
    {
        return manager;
    }
    // Detached test sessions have no host catalog factory to consult.
    let runtime = crate::runtime_auth::runtime_for_config(
        &session.services.runtime_auth,
        config,
        Arc::clone(&credentials),
        &session.installation_id,
    );
    let manager = crate::thread_manager::build_models_manager(config, credentials, runtime);
    manager.for_originator(originator).unwrap_or(manager)
}

/// Builds the base config snapshot for a newly spawned sub-agent.
///
/// The returned config starts from the parent's effective config and then refreshes the
/// model selection and reasoning settings captured for the invoking step, plus the turn's
/// runtime approval policy, sandbox, and cwd. Role-specific overrides are layered
/// after this step; skipping this helper and cloning stale config state directly can send the child
/// agent out with the wrong provider or runtime policy.
pub(crate) fn build_agent_spawn_config(
    base_instructions: &BaseInstructions,
    step_context: &StepContext,
) -> Result<Config, String> {
    let mut config = build_agent_shared_config(step_context.turn.as_ref())?;
    let settings = &step_context.settings;
    config.model = Some(settings.model_info.slug.clone());
    config.model_reasoning_effort = settings.effective_reasoning_effort();
    config.model_reasoning_summary = Some(settings.reasoning_summary);
    config.base_instructions = Some(base_instructions.text.clone());
    config.base_instructions_provenance = base_instructions.provenance.clone();
    Ok(config)
}

pub(crate) fn build_agent_resume_config(turn: &TurnContext) -> Result<Config, String> {
    let mut config = build_agent_shared_config(turn)?;
    // For resume, keep base instructions sourced from rollout/session metadata.
    config.base_instructions = None;
    config.base_instructions_provenance = None;
    Ok(config)
}

fn build_agent_shared_config(turn: &TurnContext) -> Result<Config, String> {
    let base_config = turn.config.clone();
    let mut config = (*base_config).clone();
    if config
        .features
        .enabled(Feature::SubagentDefaultContextLimits)
    {
        config.model_context_window = None;
        config.model_auto_compact_token_limit = None;
    }
    // Preserve activation for history forks without freezing the parent's model-owned prompts.
    // Fresh child startup restores configured preferences from the retained snapshot.
    config.token_budget = turn.configured_token_budget.clone();
    config.model = Some(turn.model_info().slug.clone());
    config.model_provider = turn.provider.info().clone();
    config.model_reasoning_effort = turn
        .reasoning_effort()
        .or(turn.model_info().default_reasoning_level.as_ref())
        .cloned();
    config.model_reasoning_summary = Some(turn.reasoning_summary());
    config.developer_instructions = turn.developer_instructions.clone();
    if turn.multi_agent_version == MultiAgentVersion::V2
        && let Some(developer_instructions) = turn
            .config
            .multi_agent_v2
            .subagent_developer_instructions
            .clone()
    {
        config.developer_instructions = Some(developer_instructions);
    }
    apply_spawn_agent_runtime_overrides(&mut config, turn)?;

    Ok(config)
}

fn reject_full_fork_agent_type_override(agent_type: Option<&str>) -> Result<(), String> {
    if agent_type.is_some() {
        return Err(
            "Full-history forked agents inherit the parent agent type; omit agent_type, or spawn without a full-history fork.".to_string());
    }
    Ok(())
}

/// Copies runtime-only turn state onto a child config before it is handed to `AgentControl`.
///
/// These values are chosen by the live turn rather than persisted config, so leaving them stale can
/// make a child agent disagree with its parent about approval policy, cwd, or sandboxing.
fn apply_spawn_agent_runtime_overrides(
    config: &mut Config,
    turn: &TurnContext,
) -> Result<(), String> {
    config
        .permissions
        .approval_policy
        .set(turn.approval_policy())
        .map_err(|err| format!("approval_policy is invalid: {err}"))?;
    config.approvals_reviewer = turn.config.approvals_reviewer;
    #[allow(deprecated)]
    let turn_cwd = turn.cwd.clone();
    config.cwd = turn_cwd;
    config
        .permissions
        .set_permission_profile_from_session_snapshot(
            turn.config
                .permissions
                .permission_profile_state()
                .snapshot(),
        )
        .map_err(|err| format!("permission_profile is invalid: {err}"))?;
    Ok(())
}

async fn apply_requested_spawn_agent_model_overrides(
    step_context: &StepContext,
    config: &mut Config,
    requested_model: Option<&str>,
    requested_reasoning_effort: Option<ReasoningEffort>,
    models_manager: &SharedModelsManager,
    provider_explicit: bool,
    provider_switched: bool,
) -> Result<(), String> {
    let turn = step_context.turn.as_ref();
    let requested_model = requested_model.or_else(|| {
        (!provider_switched)
            .then_some(turn.config.agent_default_subagent_model.as_deref())
            .flatten()
    });
    let requested_reasoning_effort = requested_reasoning_effort.or_else(|| {
        (!provider_switched)
            .then(|| turn.config.agent_default_subagent_reasoning_effort.clone())
            .flatten()
    });
    if requested_model.is_none() && requested_reasoning_effort.is_none() {
        return Ok(());
    }

    if let Some(requested_model) = requested_model {
        let available_models = models_manager
            .list_models(RefreshStrategy::Offline, config.http_client_factory())
            .await;
        let selected_model_name = match find_spawn_agent_model_name(
            &available_models,
            requested_model,
            turn.multi_agent_version,
        ) {
            Ok(model) => model,
            Err(_)
                if provider_explicit
                    && config.model_provider_id != "openai"
                    && !available_models
                        .iter()
                        .any(|model| model.model == requested_model)
                    && !requested_model.trim().is_empty() =>
            {
                requested_model.to_string()
            }
            Err(err) => return Err(err),
        };
        let selected_model_info = models_manager
            .get_model_info(&selected_model_name, &config.to_models_manager_config())
            .await;

        config.model = Some(selected_model_name.clone());
        if let Some(reasoning_effort) = requested_reasoning_effort {
            if selected_model_info.origin != ModelOrigin::Synthetic {
                validate_spawn_agent_reasoning_effort(
                    &selected_model_name,
                    &selected_model_info.supported_reasoning_levels,
                    &reasoning_effort,
                )?;
            }
            config.model_reasoning_effort = Some(reasoning_effort);
        } else {
            config.model_reasoning_effort = selected_model_info.default_reasoning_level.clone();
        }

        return Ok(());
    }

    if let Some(reasoning_effort) = requested_reasoning_effort {
        validate_spawn_agent_reasoning_effort(
            &step_context.settings.model_info.slug,
            &step_context.settings.model_info.supported_reasoning_levels,
            &reasoning_effort,
        )?;
        config.model_reasoning_effort = Some(reasoning_effort);
    }

    Ok(())
}

pub(crate) async fn apply_spawn_agent_service_tier(
    session: &Session,
    config: &mut Config,
) -> Result<(), String> {
    let Some(service_tier) = session
        .services
        .agent_control
        .service_tier(&config.model_provider_id)
    else {
        config.service_tier = None;
        return Ok(());
    };
    if service_tier == SERVICE_TIER_DEFAULT_REQUEST_VALUE {
        config.service_tier = Some(service_tier);
        return Ok(());
    }

    let model = config.model.clone().ok_or_else(|| {
        "spawn_agent could not resolve the child model for service tier validation".to_string()
    })?;
    let model_info = session
        .services
        .models_manager
        .get_model_info(model.as_str(), &config.to_models_manager_config())
        .await;

    config.service_tier = model_info
        .supports_service_tier(service_tier.as_str())
        .then_some(service_tier);
    Ok(())
}

async fn apply_spawn_agent_role(
    models_manager: &SharedModelsManager,
    config: &mut Config,
    role_name: Option<&str>,
) -> Result<(), String> {
    let previous_model = config.model.clone();
    let previous_reasoning_effort = config.model_reasoning_effort.clone();
    apply_role_to_config(config, role_name).await?;
    if config.model == previous_model && config.model_reasoning_effort == previous_reasoning_effort
    {
        return Ok(());
    }

    let Some(reasoning_effort) = config.model_reasoning_effort.clone() else {
        return Ok(());
    };
    let model = config.model.clone().ok_or_else(|| {
        "spawn_agent could not resolve the child model for reasoning effort validation".to_string()
    })?;
    let model_info = models_manager
        .get_model_info(&model, &config.to_models_manager_config())
        .await;
    if model_info.origin == ModelOrigin::Synthetic {
        return Ok(());
    }

    validate_spawn_agent_reasoning_effort(
        &model,
        &model_info.supported_reasoning_levels,
        &reasoning_effort,
    )
}

fn find_spawn_agent_model_name(
    available_models: &[ModelPreset],
    requested_model: &str,
    multi_agent_version: MultiAgentVersion,
) -> Result<String, String> {
    available_models
        .iter()
        .find(|model| {
            model.model == requested_model
                && model_supports_multi_agent_backend(model, multi_agent_version)
        })
        .map(|model| model.model.clone())
        .ok_or_else(|| {
            let available = available_models
                .iter()
                .filter(|model| model.show_in_picker)
                .filter(|model| model_supports_multi_agent_backend(model, multi_agent_version))
                .take(MAX_SPAWN_AGENT_MODEL_OVERRIDES)
                .map(|model| model.model.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "Unknown model `{requested_model}` for spawn_agent. Available models: {available}"
            )
        })
}

fn validate_spawn_agent_reasoning_effort(
    model: &str,
    supported_reasoning_levels: &[ReasoningEffortPreset],
    requested_reasoning_effort: &ReasoningEffort,
) -> Result<(), String> {
    if supported_reasoning_levels
        .iter()
        .any(|preset| &preset.effort == requested_reasoning_effort)
    {
        return Ok(());
    }

    let supported = supported_reasoning_levels
        .iter()
        .map(|preset| preset.effort.to_string())
        .collect::<Vec<_>>()
        .join(", ");
    Err(format!(
        "Reasoning effort `{requested_reasoning_effort}` is not supported for model `{model}`. Supported reasoning efforts: {supported}"
    ))
}
