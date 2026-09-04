//! ORCHESTRATE workspace panels (T-0029).
//!
//! 4 sub-tabs:
//!   - PLAN: operation composer (op_queue + strategy + max_loops + timeout)
//!   - EXECUTE: live progress bar + op stream
//!   - DIFF: before/after file list + inline diff
//!   - RESULTS: op results table + export + feedback

use crate::modes::Mode;
use crate::state::AppState;
use serde::{Deserialize, Serialize};

/// The 4 sub-tabs of the ORCHESTRATE workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum OrchestrateTab {
    Plan,
    Execute,
    Diff,
    Results,
}

impl OrchestrateTab {
    /// Cycle order: Plan → Execute → Diff → Results → Plan.
    pub fn cycle(self) -> Self {
        match self {
            Self::Plan    => Self::Execute,
            Self::Execute => Self::Diff,
            Self::Diff    => Self::Results,
            Self::Results => Self::Plan,
        }
    }
    pub fn from_index(i: usize) -> Self {
        match i % 4 {
            0 => Self::Plan,
            1 => Self::Execute,
            2 => Self::Diff,
            3 => Self::Results,
            _ => unreachable!(),
        }
    }
    pub fn index(self) -> usize {
        match self {
            Self::Plan    => 0,
            Self::Execute => 1,
            Self::Diff    => 2,
            Self::Results => 3,
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            Self::Plan    => "PLAN",
            Self::Execute => "EXECUTE",
            Self::Diff    => "DIFF",
            Self::Results => "RESULTS",
        }
    }
}

impl std::fmt::Display for OrchestrateTab {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

/// Strategy selection for the kernel context (matches SS_ContextStrategy in FFI).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Strategy {
    Delta,
    Targeted,
    Full,
    Symbolic,
}

impl Strategy {
    pub fn variants() -> [Self; 4] {
        [Self::Delta, Self::Targeted, Self::Full, Self::Symbolic]
    }
    pub fn label(self) -> &'static str {
        match self {
            Self::Delta    => "DELTA",
            Self::Targeted => "TARGETED",
            Self::Full     => "FULL",
            Self::Symbolic => "SYMBOLIC",
        }
    }
}

impl std::fmt::Display for Strategy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

/// One operation in the PLAN tab's op_queue.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueuedOp {
    pub id: u32,
    pub op_type: crate::ffi::SS_OpType,
    pub path: String,
    pub pattern: Option<String>,
    pub line_start: Option<u32>,
    pub line_end: Option<u32>,
    pub max_results: Option<u32>,
}

impl QueuedOp {
    /// Read op on `path` (optional line range).
    pub fn read(id: u32, path: impl Into<String>, line_start: Option<u32>, line_end: Option<u32>) -> Self {
        Self {
            id, op_type: crate::ffi::SS_OpType::READ,
            path: path.into(), pattern: None,
            line_start, line_end, max_results: None,
        }
    }
    /// Grep op (POSIX ERE pattern, optional max_results).
    pub fn grep(id: u32, path: impl Into<String>, pattern: impl Into<String>, max_results: Option<u32>) -> Self {
        Self {
            id, op_type: crate::ffi::SS_OpType::GREP,
            path: path.into(), pattern: Some(pattern.into()),
            line_start: None, line_end: None, max_results,
        }
    }
    /// AST parse op (pattern = "" | "sexp").
    pub fn ast_parse(id: u32, path: impl Into<String>, pattern: impl Into<String>) -> Self {
        Self {
            id, op_type: crate::ffi::SS_OpType::AST_PARSE,
            path: path.into(), pattern: Some(pattern.into()),
            line_start: None, line_end: None, max_results: None,
        }
    }
    /// Symbol summary op (doc layer).
    pub fn symbol_summary(id: u32, path: impl Into<String>, name: impl Into<String>) -> Self {
        Self {
            id, op_type: crate::ffi::SS_OpType::SYMBOL_SUMMARY,
            path: path.into(), pattern: Some(name.into()),
            line_start: None, line_end: None, max_results: None,
        }
    }
}

/// PLAN tab state: op_queue + strategy + max_loops + timeout.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlanState {
    pub op_queue: Vec<QueuedOp>,
    pub strategy: Strategy,
    pub target_paths: Vec<String>,
    pub max_loops: u32,
    pub timeout_sec: u32,
    /// Monotonic id used when adding new ops.
    next_op_id: u32,
}

impl Default for PlanState {
    fn default() -> Self {
        Self {
            op_queue: Vec::new(),
            strategy: Strategy::Delta,
            target_paths: Vec::new(),
            max_loops: 3,
            timeout_sec: 60,
            next_op_id: 1,
        }
    }
}

impl PlanState {
    /// Allocate a new op id.
    pub fn next_id(&mut self) -> u32 {
        let id = self.next_op_id;
        self.next_op_id += 1;
        id
    }

    /// Move an op from src_index to dst_index (drag-reorder).
    pub fn reorder(&mut self, src_index: usize, dst_index: usize) {
        if src_index >= self.op_queue.len() || dst_index >= self.op_queue.len() {
            return;
        }
        if src_index == dst_index {
            return;
        }
        let op = self.op_queue.remove(src_index);
        let insert_at = if dst_index > src_index { dst_index - 1 } else { dst_index };
        self.op_queue.insert(insert_at.min(self.op_queue.len()), op);
    }

    /// Remove the op at the given index.
    pub fn remove(&mut self, index: usize) -> Option<QueuedOp> {
        if index < self.op_queue.len() {
            Some(self.op_queue.remove(index))
        } else {
            None
        }
    }
}

/// EXECUTE tab state: progress + streamed output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecuteState {
    /// 0.0..=1.0; None = idle.
    pub progress: Option<f32>,
    /// Streamed op output lines (most recent at the end).
    pub stream: Vec<String>,
    /// Index of the currently executing op, if any.
    pub current_op: Option<u32>,
    /// true while a plan is running.
    pub running: bool,
}

impl Default for ExecuteState {
    fn default() -> Self {
        Self {
            progress: None,
            stream: Vec::new(),
            current_op: None,
            running: false,
        }
    }
}

impl ExecuteState {
    /// Start executing a plan with `n` ops.
    pub fn start(&mut self, op_count: usize) {
        self.running = true;
        self.progress = Some(0.0);
        self.stream.clear();
        self.current_op = None;
        self.stream.push(format!("starting plan with {} op(s)", op_count));
    }

    /// Mark an op as started (called by the runner).
    pub fn begin_op(&mut self, op_id: u32) {
        self.current_op = Some(op_id);
        self.stream.push(format!(">> op {} begin", op_id));
    }

    /// Mark an op as finished.
    pub fn end_op(&mut self, op_id: u32, total: usize) {
        let done = self.stream.iter().filter(|l| l.contains("begin")).count();
        self.progress = Some((done as f32 / total.max(1) as f32).clamp(0.0, 1.0));
        self.stream.push(format!("<< op {} done", op_id));
        if done >= total {
            self.running = false;
            self.current_op = None;
        }
    }

    /// Append a log line to the op stream.
    pub fn log(&mut self, line: impl Into<String>) {
        self.stream.push(line.into());
        // cap the stream to avoid unbounded growth
        const MAX: usize = 4096;
        if self.stream.len() > MAX {
            let drop = self.stream.len() - MAX;
            self.stream.drain(0..drop);
        }
    }
}

/// One diff entry for the DIFF tab: a file that was added/modified/deleted.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiffEntry {
    pub path: String,
    pub kind: DiffKind,
    /// Inline diff hunks (one per changed region); simple text for now.
    pub hunks: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiffKind {
    Added,
    Modified,
    Deleted,
}

impl DiffKind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Added    => "ADDED",
            Self::Modified => "MODIFIED",
            Self::Deleted  => "DELETED",
        }
    }
}

/// DIFF tab state: before/after file list.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DiffState {
    pub entries: Vec<DiffEntry>,
    /// Index of the currently selected entry.
    pub selected: usize,
    /// Optional hunk scroll offset.
    pub hunk_scroll: u16,
}

impl DiffState {
    /// Get the currently focused entry, if any.
    pub fn selected_entry(&self) -> Option<&DiffEntry> {
        self.entries.get(self.selected)
    }
    /// Move selection by delta (+1 down, -1 up), clamped.
    pub fn move_selection(&mut self, delta: i32) {
        if self.entries.is_empty() { return; }
        let n = self.entries.len() as i32;
        let next = (self.selected as i32 + delta).clamp(0, n - 1);
        self.selected = next as usize;
    }
}

/// One result row for the RESULTS tab.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResultRow {
    pub op_id: u32,
    pub op_type: String,
    pub path: String,
    pub exit_code: i32,
    pub summary: String,
    /// User feedback on the result: positive / negative / none.
    pub feedback: Feedback,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Feedback {
    None,
    Positive,
    Negative,
}

impl Feedback {
    pub fn label(self) -> &'static str {
        match self {
            Self::None     => "—",
            Self::Positive => "✓ good",
            Self::Negative => "✗ bad",
        }
    }
}

/// RESULTS tab state: the result table + scroll.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ResultsState {
    pub rows: Vec<ResultRow>,
    pub selected: usize,
    pub scroll: u16,
    /// Set after `Export` is pressed; bin-layer consumes.
    pub export_requested: bool,
}

impl ResultsState {
    pub fn selected_row(&self) -> Option<&ResultRow> {
        self.rows.get(self.selected)
    }
    pub fn move_selection(&mut self, delta: i32) {
        if self.rows.is_empty() { return; }
        let n = self.rows.len() as i32;
        let next = (self.selected as i32 + delta).clamp(0, n - 1);
        self.selected = next as usize;
    }
    /// Apply a feedback signal to the selected row.
    pub fn feedback_selected(&mut self, fb: Feedback) {
        if let Some(row) = self.rows.get_mut(self.selected) {
            row.feedback = fb;
        }
    }
}

/// Full ORCHESTRATE workspace state (PLAN + EXECUTE + DIFF + RESULTS).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OrchestrateState {
    pub current_tab: OrchestrateTab,
    pub plan: PlanState,
    pub execute: ExecuteState,
    pub diff: DiffState,
    pub results: ResultsState,
}

impl OrchestrateState {
    /// Reset all panels to defaults (used when switching out of ORCHESTRATE).
    pub fn clear(&mut self) {
        *self = Self::default();
    }
}

/// Hooks the workspace into the AppState: mode==Orchestrate → use these panels.
pub fn ensure_for_mode(state: &mut AppState) {
    if state.mode == Mode::Orchestrate && state.orchestrate.is_none() {
        state.orchestrate = Some(OrchestrateState::default());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tab_cycle_and_index_are_inverse() {
        for i in 0..4 {
            let t = OrchestrateTab::from_index(i);
            assert_eq!(t.index(), i);
            assert_eq!(t.cycle().cycle().cycle().cycle(), t);
        }
    }

    #[test]
    fn plan_state_reorder() {
        let mut p = PlanState::default();
        let id1 = p.next_id();
        p.op_queue.push(QueuedOp::read(id1, "a.rs", None, None));
        let id2 = p.next_id();
        p.op_queue.push(QueuedOp::grep(id2, "b.rs", "TODO", Some(50)));
        let id3 = p.next_id();
        p.op_queue.push(QueuedOp::ast_parse(id3, "c.rs", "sexp"));

        assert_eq!(p.op_queue.len(), 3);
        p.reorder(0, 2);
        // order is now: b, c, a
        assert_eq!(p.op_queue[0].op_type, crate::ffi::SS_OpType::GREP);
        assert_eq!(p.op_queue[2].op_type, crate::ffi::SS_OpType::READ);
    }

    #[test]
    fn execute_progress() {
        let mut e = ExecuteState::default();
        e.start(2);
        assert!(e.running);
        e.begin_op(1);
        e.end_op(1, 2);
        e.begin_op(2);
        e.end_op(2, 2);
        assert!(!e.running);
        assert!(e.progress.unwrap() > 0.99);
    }

    #[test]
    fn diff_move_selection_clamps() {
        let mut d = DiffState::default();
        d.entries.push(DiffEntry { path: "a".into(), kind: DiffKind::Added,    hunks: vec![] });
        d.entries.push(DiffEntry { path: "b".into(), kind: DiffKind::Modified, hunks: vec![] });
        d.move_selection(1);
        assert_eq!(d.selected, 1);
        d.move_selection(10);
        assert_eq!(d.selected, 1);
        d.move_selection(-5);
        assert_eq!(d.selected, 0);
    }

    #[test]
    fn results_feedback() {
        let mut r = ResultsState::default();
        r.rows.push(ResultRow {
            op_id: 1, op_type: "READ".into(), path: "x.rs".into(),
            exit_code: 0, summary: "ok".into(), feedback: Feedback::None,
        });
        r.feedback_selected(Feedback::Positive);
        assert_eq!(r.selected_row().unwrap().feedback, Feedback::Positive);
    }

    #[test]
    fn strategies_listed() {
        let s = Strategy::variants();
        assert_eq!(s.len(), 4);
        assert_eq!(s[0].label(), "DELTA");
        assert_eq!(s[3].label(), "SYMBOLIC");
    }
}
