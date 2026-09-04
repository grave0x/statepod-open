//! Application runtime state (the model — not UI widget tree).

use crate::design::Design;
use crate::layout::{FooterStatusBar, HeaderBar, Layout};
use crate::modes::Mode;
use crate::workspace::OrchestrateState;
use serde::{Deserialize, Serialize};

/// Root of the deserialised wireframe document.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Wireframe {
    #[serde(rename = "$schema")]
    pub schema:     String,
    pub name:       String,
    pub version:    String,
    pub description: String,
    pub design:     Design,
    pub layout:     Layout,
    pub header_bar: HeaderBar,
    #[serde(rename = "footer_status_bar")]
    pub footer:     FooterStatusBar,
    pub modes:      Vec<String>,
    #[serde(flatten)]
    pub extra:      serde_json::Value,
}

/// SWARMTUI application state — everything that changes at runtime.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppState {
    /// Current top-level mode (ORCHESTRATE … MESH).
    pub mode: Mode,

    /// Current sub-mode / tab within the center pane (mode-dependent).
    pub subtab: Option<String>,

    /// Currently selected node id (None = nothing selected).
    pub selected_node: Option<String>,

    /// 0-based vertical scroll offset per pane id.
    pub scroll_offsets: std::collections::HashMap<String, u16>,

    /// Whether the left pane filter input is focused.
    pub filter_focused: bool,

    /// Right-pane active tab ("NODE" | "LOGS" | "REGISTRY" | "GOV" | "MESH").
    pub right_tab: String,

    /// ORCHESTRATE mode workspace state (PLAN/EXECUTE/DIFF/RESULTS tabs).
    /// None when mode is not Orchestrate; populated lazily by ensure_for_mode().
    pub orchestrate: Option<OrchestrateState>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            mode:            Mode::Orchestrate,
            subtab:          None,
            selected_node:   None,
            scroll_offsets:  std::collections::HashMap::new(),
            filter_focused:  false,
            right_tab:       "NODE".to_string(),
            orchestrate:     None,
        }
    }
}

impl Wireframe {
    /// Parse a wireframe document from a JSON string.
    pub fn from_json(s: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(s)
    }

    /// Parse a wireframe document from raw bytes.
    pub fn from_slice(b: &[u8]) -> Result<Self, serde_json::Error> {
        serde_json::from_slice(b)
    }

    /// Look up the numeric index of a mode in the wireframe list.
    pub fn mode_index(&self, mode: Mode) -> Option<u8> {
        let needle = mode.to_string();
        self.modes.iter().position(|m| m == &needle).map(|i| i as u8)
    }

    /// Get the design palette (helper for the render layer).
    pub fn palette(&self) -> &crate::design::ColorPalette {
        &self.design.color_palette
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WF: &str = include_str!("../wireframes.json");

    #[test]
    fn parses_wireframes_json() {
        let wf = Wireframe::from_json(WF).expect("valid wireframes.json");
        assert_eq!(wf.name, "swarmtui");
        assert_eq!(wf.modes.len(), 7);
        assert_eq!(wf.layout.panes.len(), 3);
        // mode index lookup
        assert_eq!(wf.mode_index(Mode::Orchestrate), Some(0));
        assert_eq!(wf.mode_index(Mode::Mesh), Some(6));
    }

    #[test]
    fn app_state_defaults_to_orchestrate() {
        let s = AppState::default();
        assert_eq!(s.mode, Mode::Orchestrate);
        assert_eq!(s.right_tab, "NODE");
        assert!(s.selected_node.is_none());
    }
}
