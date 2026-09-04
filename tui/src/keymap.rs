//! Keymap + key routing for SWARMTUI.
//!
//! T-0022: Define a state machine on top of `AppState` that handles global keys
//! and per-pane focus routing. Rendering (T-0023+) layers on top of this.

use crate::modes::Mode;
use crate::state::AppState;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

/// Where focus currently lives. Determines which sub-pane receives typed input.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Pane {
    Left,
    Center,
    Right,
}

impl Pane {
    /// Cycle: Left → Center → Right → Left.
    pub fn cycle(self) -> Self {
        match self {
            Pane::Left   => Pane::Center,
            Pane::Center => Pane::Right,
            Pane::Right  => Pane::Left,
        }
    }
}

/// A single global action produced by the keymap.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// Switch to a top-level mode (Ctrl+1..Ctrl+7).
    SwitchMode(Mode),
    /// Cycle focus to the next pane (Tab).
    CyclePane,
    /// Focus the left-pane filter input ("/" key, only when left pane is focused).
    FocusFilter,
    /// Quit (Ctrl+Q).
    Quit,
    /// Save state (Ctrl+S).
    Save,
    /// Clear logs (Ctrl+L).
    ClearLogs,
    /// Toggle help overlay ("?").
    ToggleHelp,
    /// Move focus / selection by delta (arrow keys, h/j/k/l, PageUp/PageDown).
    Move { dx: i16, dy: i16 },
    /// Activate the currently focused item (Enter).
    Activate,
    /// Nothing — input was consumed but produced no action (e.g. modifier alone).
    Noop,
}

/// Global keymap: turn a crossterm KeyEvent into an Action, given current state.
///
/// `Tab` cycles panes, `Ctrl+1..7` switches modes, `Ctrl+Q` quits, `Ctrl+S` saves,
/// `Ctrl+L` clears logs, `?` toggles help, `/` focuses the filter when left pane is active,
/// arrows/hjkl/PgUp/PgDn move, Enter activates, Esc clears filter focus.
pub fn map_key(event: KeyEvent, state: &AppState) -> Action {
    let mods = event.modifiers;
    let code = event.code;

    // ── Ctrl-modified global keys ───────────────────────────────────────
    if mods.contains(KeyModifiers::CONTROL) {
        match code {
            KeyCode::Char('q') | KeyCode::Char('Q') => return Action::Quit,
            KeyCode::Char('s') | KeyCode::Char('S') => return Action::Save,
            KeyCode::Char('l') | KeyCode::Char('L') => return Action::ClearLogs,
            KeyCode::Char('c') | KeyCode::Char('C') => return Action::Quit, // Ctrl+C also quits
            KeyCode::Char('1') => return Action::SwitchMode(Mode::Orchestrate),
            KeyCode::Char('2') => return Action::SwitchMode(Mode::Monitor),
            KeyCode::Char('3') => return Action::SwitchMode(Mode::Experiment),
            KeyCode::Char('4') => return Action::SwitchMode(Mode::AutoImprove),
            KeyCode::Char('5') => return Action::SwitchMode(Mode::Research),
            KeyCode::Char('6') => return Action::SwitchMode(Mode::Eval),
            KeyCode::Char('7') => return Action::SwitchMode(Mode::Mesh),
            _ => return Action::Noop,
        }
    }

    // ── Plain keys (no Ctrl) ────────────────────────────────────────────
    match code {
        KeyCode::Tab => Action::CyclePane,
        KeyCode::BackTab => Action::CyclePane, // Shift+Tab also cycles (reverse handled at higher layer)
        KeyCode::Char('?') => Action::ToggleHelp,
        KeyCode::Char('/') if !state.filter_focused => Action::FocusFilter,
        KeyCode::Esc if state.filter_focused => Action::FocusFilter, // toggle off
        // Movement
        KeyCode::Up    | KeyCode::Char('k') => Action::Move { dx:  0, dy: -1 },
        KeyCode::Down  | KeyCode::Char('j') => Action::Move { dx:  0, dy:  1 },
        KeyCode::Left  | KeyCode::Char('h') => Action::Move { dx: -1, dy:  0 },
        KeyCode::Right | KeyCode::Char('l') => Action::Move { dx:  1, dy:  0 },
        KeyCode::PageUp   => Action::Move { dx:  0, dy: -10 },
        KeyCode::PageDown => Action::Move { dx:  0, dy:  10 },
        KeyCode::Home     => Action::Move { dx:  0, dy: -1000 },
        KeyCode::End      => Action::Move { dx:  0, dy:  1000 },
        KeyCode::Enter => Action::Activate,
        _ => Action::Noop,
    }
}

/// Apply an Action to the AppState, mutating it in place. Returns `true` if the
/// caller should quit the main event loop.
pub fn apply_action(state: &mut AppState, focus: &mut Pane, action: Action) -> bool {
    match action {
        Action::SwitchMode(m) => {
            state.mode = m;
            // Reset center subtab to None on mode switch (per spec).
            state.subtab = None;
            false
        }
        Action::CyclePane => {
            *focus = focus.cycle();
            false
        }
        Action::FocusFilter => {
            // Focus filter only meaningful when left pane is active.
            if *focus == Pane::Left {
                state.filter_focused = !state.filter_focused;
            }
            false
        }
        Action::Quit => true,
        Action::Save => {
            // Persist happens at the bin-layer; here we just record the request.
            // Mark by setting a sentinel that the bin can observe.
            state.scroll_offsets.insert("__save_request__".to_string(), 1);
            false
        }
        Action::ClearLogs => {
            state.scroll_offsets.insert("__clear_logs__".to_string(), 1);
            false
        }
        Action::ToggleHelp => {
            state.scroll_offsets.insert("__help_toggle__".to_string(), 1);
            false
        }
        Action::Move { dx, dy } => {
            // Scroll the focused pane.
            let key = match focus {
                Pane::Left   => "left".to_string(),
                Pane::Center => "center".to_string(),
                Pane::Right  => "right".to_string(),
            };
            let cur = state.scroll_offsets.get(&key).copied().unwrap_or(0);
            let next = (cur as i32 + dy as i32).max(0).min(u16::MAX as i32) as u16;
            state.scroll_offsets.insert(key, next);
            // dx used for cross-pane navigation (right pane tabs etc.) at higher layers.
            let _ = dx;
            false
        }
        Action::Activate => {
            state.scroll_offsets.insert("__activate__".to_string(), 1);
            false
        }
        Action::Noop => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    fn key(code: KeyCode, mods: KeyModifiers) -> KeyEvent {
        KeyEvent::new(code, mods)
    }

    #[test]
    fn ctrl_1_to_7_switches_modes() {
        let s = AppState::default();
        for (n, expected) in [
            (1, Mode::Orchestrate), (2, Mode::Monitor), (3, Mode::Experiment),
            (4, Mode::AutoImprove), (5, Mode::Research), (6, Mode::Eval),
            (7, Mode::Mesh),
        ] {
            let a = map_key(key(KeyCode::Char(std::char::from_digit(n, 10).unwrap()), KeyModifiers::CONTROL), &s);
            assert_eq!(a, Action::SwitchMode(expected));
        }
    }

    #[test]
    fn tab_cycles_panes() {
        let s = AppState::default();
        assert_eq!(map_key(key(KeyCode::Tab, KeyModifiers::NONE), &s), Action::CyclePane);
    }

    #[test]
    fn slash_focuses_filter_when_left_focused() {
        let s = AppState::default();
        assert_eq!(
            map_key(key(KeyCode::Char('/'), KeyModifiers::NONE), &s),
            Action::FocusFilter
        );
    }

    #[test]
    fn quit_on_ctrl_q() {
        let s = AppState::default();
        assert_eq!(
            map_key(key(KeyCode::Char('q'), KeyModifiers::CONTROL), &s),
            Action::Quit
        );
    }

    #[test]
    fn arrow_keys_move_focus() {
        let s = AppState::default();
        assert_eq!(map_key(key(KeyCode::Up, KeyModifiers::NONE),    &s), Action::Move { dx:  0, dy: -1 });
        assert_eq!(map_key(key(KeyCode::Down, KeyModifiers::NONE),  &s), Action::Move { dx:  0, dy:  1 });
        assert_eq!(map_key(key(KeyCode::Left, KeyModifiers::NONE),  &s), Action::Move { dx: -1, dy:  0 });
        assert_eq!(map_key(key(KeyCode::Right, KeyModifiers::NONE), &s), Action::Move { dx:  1, dy:  0 });
    }

    #[test]
    fn hjkl_move_too() {
        let s = AppState::default();
        assert_eq!(map_key(key(KeyCode::Char('h'), KeyModifiers::NONE), &s), Action::Move { dx: -1, dy:  0 });
        assert_eq!(map_key(key(KeyCode::Char('j'), KeyModifiers::NONE), &s), Action::Move { dx:  0, dy:  1 });
        assert_eq!(map_key(key(KeyCode::Char('k'), KeyModifiers::NONE), &s), Action::Move { dx:  0, dy: -1 });
        assert_eq!(map_key(key(KeyCode::Char('l'), KeyModifiers::NONE), &s), Action::Move { dx:  1, dy:  0 });
    }

    #[test]
    fn apply_action_quit_returns_true() {
        let mut s = AppState::default();
        let mut f = Pane::Left;
        assert!(apply_action(&mut s, &mut f, Action::Quit));
    }

    #[test]
    fn apply_action_switch_mode_resets_subtab() {
        let mut s = AppState::default();
        s.subtab = Some("PLAN".to_string());
        let mut f = Pane::Left;
        apply_action(&mut s, &mut f, Action::SwitchMode(Mode::Monitor));
        assert_eq!(s.mode, Mode::Monitor);
        assert!(s.subtab.is_none());
    }

    #[test]
    fn apply_action_cycles_focus() {
        let mut s = AppState::default();
        let mut f = Pane::Left;
        apply_action(&mut s, &mut f, Action::CyclePane);
        assert_eq!(f, Pane::Center);
        apply_action(&mut s, &mut f, Action::CyclePane);
        assert_eq!(f, Pane::Right);
        apply_action(&mut s, &mut f, Action::CyclePane);
        assert_eq!(f, Pane::Left);
    }

    #[test]
    fn focus_filter_only_when_left_focused() {
        let mut s = AppState::default();
        let mut f = Pane::Center;
        apply_action(&mut s, &mut f, Action::FocusFilter);
        // No-op when left is not focused.
        assert!(!s.filter_focused);
        f = Pane::Left;
        apply_action(&mut s, &mut f, Action::FocusFilter);
        assert!(s.filter_focused);
    }

    #[test]
    fn save_records_request() {
        let mut s = AppState::default();
        let mut f = Pane::Left;
        apply_action(&mut s, &mut f, Action::Save);
        assert_eq!(s.scroll_offsets.get("__save_request__"), Some(&1));
    }
}
