//! Generic block / panel types used throughout center and right panes.
//!
//! These correspond to the `"panels"` arrays in center/right pane definitions.

use serde::{Deserialize, Serialize};

/// The set of named block/panel identifiers used across all panes.
/// Each `BlockId` maps to a concrete `Block` that knows how to render itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum BlockType {
    // ── Left pane ──────────────────────────────────────────────────────────
    FilterBar,
    NodeTreeView,
    SelectedNodeInfo,
    AddNodeButtons,

    // ── Center pane (ORCHESTRATE sub-mode) ─────────────────────────────────
    OperationComposer,
    ExecuteRunner,
    DiffViewer,
    ResultsTable,

    // ── Center pane (MONITOR sub-mode) ──────────────────────────────────────
    LiveMetrics,
    Alerts,
    NodeFocusLog,

    // ── Center pane (EXPERIMENT sub-mode) ──────────────────────────────────
    TemplateGallery,
    ExperimentBuilder,
    RunQueue,
    ComparisonTable,

    // ── Center pane (AUTO-IMPROVE sub-mode) ────────────────────────────────
    TargetConfig,
    IterationsLog,
    IterationDetail,
    TrendChart,

    // ── Center pane (RESEARCH sub-mode) ────────────────────────────────────
    QueryBar,
    SourceChips,
    KitBrowser,
    Synthesis,

    // ── Center pane (EVAL sub-mode) ────────────────────────────────────────
    PackSelector,
    LiveProgress,
    EvalResults,

    // ── Center pane (MESH sub-mode) ─────────────────────────────────────────
    Topology,
    PeerList,
    TrafficStats,
    PoolStatus,
    BridgeConfig,
    GovAudit,

    // ── Right pane ─────────────────────────────────────────────────────────
    SelectedNode,
    SystemInfo,
    CheckpointChain,
    LoraInfo,
    NodeActions,
    LevelFilter,
    NodeFilter,
    OpFilter,
    LogStream,
    SignatureTable,
    SparklinePerSig,
    AuditLog,
    PendingDecisions,
    IdentityChain,
    HashStatus,
    MeshTopologyDiagram,
    PeerTable,
}

impl BlockType {
    /// Parse from a snake_case wireframe panel id string.
    pub fn from_wire(s: &str) -> Option<Self> {
        match s {
            // Left
            "filter_bar"          => Some(BlockType::FilterBar),
            "node_tree_view"      => Some(BlockType::NodeTreeView),
            "selected_node_info"  => Some(BlockType::SelectedNodeInfo),
            "add_node_buttons"   => Some(BlockType::AddNodeButtons),
            // Orchestrate
            "operation_composer" => Some(BlockType::OperationComposer),
            "execute_runner"      => Some(BlockType::ExecuteRunner),
            "diff_viewer"        => Some(BlockType::DiffViewer),
            "results_table"      => Some(BlockType::ResultsTable),
            // Monitor
            "live_metrics"       => Some(BlockType::LiveMetrics),
            "alerts"             => Some(BlockType::Alerts),
            "node_focus_log"     => Some(BlockType::NodeFocusLog),
            // Experiment
            "template_gallery"   => Some(BlockType::TemplateGallery),
            "experiment_builder" => Some(BlockType::ExperimentBuilder),
            "run_queue"          => Some(BlockType::RunQueue),
            "comparison_table"   => Some(BlockType::ComparisonTable),
            // Auto-improve
            "target_config"      => Some(BlockType::TargetConfig),
            "iterations_log"     => Some(BlockType::IterationsLog),
            "iteration_detail"   => Some(BlockType::IterationDetail),
            "trend_chart"        => Some(BlockType::TrendChart),
            // Research
            "query_bar"          => Some(BlockType::QueryBar),
            "source_chips"        => Some(BlockType::SourceChips),
            "kit_browser"        => Some(BlockType::KitBrowser),
            "synthesis"          => Some(BlockType::Synthesis),
            // Eval
            "pack_selector"       => Some(BlockType::PackSelector),
            "live_progress"      => Some(BlockType::LiveProgress),
            "results"            => Some(BlockType::EvalResults),
            // Mesh
            "topology"           => Some(BlockType::Topology),
            "peer_list"          => Some(BlockType::PeerList),
            "traffic"            => Some(BlockType::TrafficStats),
            "pool"               => Some(BlockType::PoolStatus),
            "bridge"             => Some(BlockType::BridgeConfig),
            "gov_audit"          => Some(BlockType::GovAudit),
            // Right pane NODE tab
            "selected_node"      => Some(BlockType::SelectedNode),
            "system_info"        => Some(BlockType::SystemInfo),
            "checkpoint_chain"   => Some(BlockType::CheckpointChain),
            "lora_info"          => Some(BlockType::LoraInfo),
            "actions"            => Some(BlockType::NodeActions),
            // Right pane LOGS tab
            "level_filter"       => Some(BlockType::LevelFilter),
            "node_filter"        => Some(BlockType::NodeFilter),
            "op_filter"          => Some(BlockType::OpFilter),
            "log_stream"         => Some(BlockType::LogStream),
            // Right pane REGISTRY tab
            "signature_table"    => Some(BlockType::SignatureTable),
            "sparkline_per_sig"  => Some(BlockType::SparklinePerSig),
            // Right pane GOV tab
            "audit_log"          => Some(BlockType::AuditLog),
            "pending_decisions"  => Some(BlockType::PendingDecisions),
            "identity_chain"     => Some(BlockType::IdentityChain),
            "hash_status"        => Some(BlockType::HashStatus),
            // Right pane MESH tab
            "topology_diagram"   => Some(BlockType::MeshTopologyDiagram),
            "peer_table"         => Some(BlockType::PeerTable),
            _                    => None,
        }
    }
}

/// A concrete panel instance with an id and optional config payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Block {
    pub id:     String,
    #[serde(rename = "type")]
    pub block_type: BlockType,
}
