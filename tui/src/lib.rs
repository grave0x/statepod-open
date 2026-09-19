//! statetui — TUI model + keymap layer (T-0021, T-0022).
//!
//! All types are derived from `/tmp/statetui-wireframes.json`.
//! Rendering lives in the future `src/render/` module (T-0023+).

pub mod design;
pub mod ffi;
pub mod keymap;
pub mod layout;
pub mod modes;
pub mod sections;
pub mod state;
pub mod workspace;

pub use design::{ColorPalette, Design, Glyphs};
pub use keymap::{map_key, apply_action, Action, Pane};
pub use layout::{HeaderBar, HeaderRow, FooterStatusBar, Layout, Pane as LayoutPane};
pub use modes::Mode;
pub use sections::{
    block::{Block, BlockType},
    Action as SectionAction, Button, ButtonRowSection as ButtonRow, Field, KeyValueSection, NodeStatusDots,
    Section, Tab, TextInputSection, TreeSection,
};
pub use state::{AppState, Wireframe};
pub use workspace::{
    DiffEntry, DiffKind, DiffState, ExecuteState, Feedback, OrchestrateState,
    OrchestrateTab, PlanState, QueuedOp, ResultRow, ResultsState, Strategy,
};
