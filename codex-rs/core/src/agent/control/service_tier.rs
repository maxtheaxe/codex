//! Shares the root user's selected routing tier with descendants using its provider.

use super::LocalAgentControl;
use std::sync::Arc;

pub(super) struct RootServiceTier {
    provider_id: String,
    service_tier: String,
}

impl LocalAgentControl {
    /// Returns the latest root preference only within its provider boundary.
    pub(crate) fn root_service_tier(&self, provider_id: &str) -> Option<String> {
        self.runtime
            .root_service_tier
            .load_full()
            .filter(|selection| selection.provider_id == provider_id)
            .map(|selection| selection.service_tier.clone())
    }

    /// Publishes a root-owned tier without mutating individual child sessions.
    pub(crate) fn set_root_service_tier(&self, provider_id: String, service_tier: Option<String>) {
        self.runtime
            .root_service_tier
            .store(service_tier.map(|service_tier| {
                Arc::new(RootServiceTier {
                    provider_id,
                    service_tier,
                })
            }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_tier_follows_provider_and_live_root_updates() {
        let control = LocalAgentControl::default();
        let descendant = control.clone();
        control.set_root_service_tier("openai".to_string(), Some("flex".to_string()));
        assert_eq!(
            descendant.root_service_tier("openai"),
            Some("flex".to_string())
        );
        assert_eq!(descendant.root_service_tier("openrouter"), None);

        control.set_root_service_tier("openai".to_string(), Some("priority".to_string()));
        assert_eq!(descendant.root_service_tier("openrouter"), None);
        // A descendant switching back to the root provider sees the latest preference.
        assert_eq!(
            descendant.root_service_tier("openai"),
            Some("priority".to_string())
        );

        control.set_root_service_tier("openrouter".to_string(), Some("flex".to_string()));
        assert_eq!(descendant.root_service_tier("openai"), None);
        assert_eq!(
            descendant.root_service_tier("openrouter"),
            Some("flex".to_string())
        );
        control.set_root_service_tier("openrouter".to_string(), None);
        assert_eq!(descendant.root_service_tier("openrouter"), None);
    }
}
