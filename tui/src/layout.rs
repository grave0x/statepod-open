use serde::{Deserialize, Serialize};

/// Global 3-pane layout definition.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Layout {
    #[serde(rename = "type")]
    pub layout_type: String,
    pub header_rows: u8,
    pub status_rows: u8,
    pub panes: Vec<Pane>,
}

/// A single pane within the layout.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pane {
    pub id:        String,
    pub name:      String,
    pub x:         u8,
    pub w_pct:     u8,
    pub scrollable: bool,
}

/// The two-row header bar at the top of the screen.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HeaderBar {
    pub row_1: HeaderRow,
    pub row_2: HeaderRow,
}

/// One row of the header bar — each field slot holds a logical data-source key.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HeaderRow {
    pub left:   Vec<String>,
    pub center: Vec<String>,
    pub right:  Vec<String>,
}

/// The single-row footer / status bar at the bottom of the screen.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FooterStatusBar {
    pub fields: Vec<String>,
}
