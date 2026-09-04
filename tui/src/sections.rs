//! UI section / block types derived from wireframe sections.

pub mod block;

pub use block::{Block, BlockType};

/// Section id → BlockType mapping for the left-pane node-tree pane.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct LeftPane {
    pub id:       String,
    pub sections: Vec<Section>,
}

/// Section id → BlockType mapping for the right-pane inspector pane.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct RightPane {
    pub id:    String,
    pub tabs:  Vec<String>,
    pub panels: serde_json::Value, // map<tab, Vec<panel_id>>
}

// ─── Section variants ────────────────────────────────────────────────────────

/// Enum-or-struct union of all section/block types in the wireframes.
#[derive(Debug, Clone, serde::Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Section {
    TextInput(TextInputSection),
    Tree(TreeSection),
    KeyValue(KeyValueSection),
    ButtonRow(ButtonRowSection),
    // remaining types added as needed (T-0022)
}

/// Plain text input with placeholder — used in filter bars.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct TextInputSection {
    pub id:          String,
    pub placeholder: String,
    pub key:         String, // keybinding to focus
}

/// Node tree view with status glyphs and edge-badge labels.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct TreeSection {
    pub id:            String,
    pub root_label:     String,
    pub node_status_dots: NodeStatusDots,
    pub edge_badges:    Vec<String>,
    pub expand_keys:    String,
    pub navigate_keys: String,
    pub context_keys:  Vec<String>,
}

/// Maps node status keywords → display glyphs.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct NodeStatusDots {
    pub running:    String,
    pub idle:       String,
    pub offline:    String,
    pub paused:     String,
    pub escalation: String,
    pub pending:    String,
}

/// Key–value table showing selected-node metadata + action buttons.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct KeyValueSection {
    pub id:      String,
    pub fields:  Vec<String>,
    pub actions: Vec<String>,
}

/// Horizontal button row for pane-level actions (add node, scan, etc.).
#[derive(Debug, Clone, serde::Deserialize)]
pub struct ButtonRowSection {
    pub id:      String,
    pub buttons: Vec<Button>,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct Button {
    pub label: String,
    // id derived from label slug at render time
}

/// Standalone tab for tabbed panes.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct Tab {
    pub id:    String,
    pub label: String,
}

/// A single key=value row in a key-value block.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct Field {
    pub key:   String,
    pub value: Option<String>, // None = load dynamically
}

/// An action button attached to a section.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct Action {
    pub id:    String,
    pub label: String,
    pub key:   Option<String>, // optional keybinding
}
