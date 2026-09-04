use serde::{Deserialize, Serialize};

/// The 7 top-level operating modes of the SWARMTUI.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Mode {
    Orchestrate,
    Monitor,
    Experiment,
    AutoImprove,
    Research,
    Eval,
    Mesh,
}

impl Mode {
    /// Returns the index (0–6) used for keybinding routing (Ctrl+1 … Ctrl+7).
    #[inline]
    pub fn index(self) -> u8 {
        match self {
            Mode::Orchestrate => 0,
            Mode::Monitor     => 1,
            Mode::Experiment  => 2,
            Mode::AutoImprove=> 3,
            Mode::Research    => 4,
            Mode::Eval        => 5,
            Mode::Mesh        => 6,
        }
    }

    /// Parse from wireframe string (e.g. "ORCHESTRATE").
    #[inline]
    pub fn from_wire(s: &str) -> Option<Self> {
        match s {
            "ORCHESTRATE"  => Some(Mode::Orchestrate),
            "MONITOR"      => Some(Mode::Monitor),
            "EXPERIMENT"   => Some(Mode::Experiment),
            "AUTO-IMPROVE" => Some(Mode::AutoImprove),
            "RESEARCH"     => Some(Mode::Research),
            "EVAL"         => Some(Mode::Eval),
            "MESH"         => Some(Mode::Mesh),
            _              => None,
        }
    }
}

impl std::fmt::Display for Mode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Mode::Orchestrate  => write!(f, "ORCHESTRATE"),
            Mode::Monitor      => write!(f, "MONITOR"),
            Mode::Experiment   => write!(f, "EXPERIMENT"),
            Mode::AutoImprove  => write!(f, "AUTO-IMPROVE"),
            Mode::Research     => write!(f, "RESEARCH"),
            Mode::Eval         => write!(f, "EVAL"),
            Mode::Mesh         => write!(f, "MESH"),
        }
    }
}
