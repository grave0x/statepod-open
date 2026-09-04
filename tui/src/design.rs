use serde::{Deserialize, Serialize};

/// ratatui rendering engine and font configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Design {
    pub font:   String,
    pub engine: String,
    pub borders: String,
    pub color_palette: ColorPalette,
    pub glyphs: Glyphs,
}

/// Maps semantic status labels to ratatui Color names.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ColorPalette {
    pub ok:       String,
    pub warning:  String,
    pub error:    String,
    pub idle:     String,
    pub selected: String,
    pub header:   String,
    pub dim:      String,
}

/// Maps semantic glyph names to actual Unicode codepoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Glyphs {
    pub running:       String,
    pub idle:          String,
    pub offline:       String,
    pub paused:        String,
    pub escalation:    String,
    pub pending:       String,
    pub success:       String,
    pub fail:          String,
    pub in_progress:   String,
    pub ok_box:        String,
    pub mid_box:       String,
    pub low_box:       String,
    pub sparkline_chars: String,
}
