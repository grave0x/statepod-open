//! examples/wireframe_dump.rs — smoke-test: deserialise wireframes and print stats

use serde_json;
use std::fs;

fn main() {
    let text = fs::read_to_string("wireframes.json").expect("wireframes.json");
    let wf: serde_json::Value = serde_json::from_str(&text).expect("valid JSON");

    let modes = wf["modes"].as_array().expect("modes[]");
    println!("Modes ({}): {:?}", modes.len(), modes);

    let panes = wf["layout"]["panes"].as_array().expect("panes[]");
    println!("Panes ({}): {:?}", panes.len(), panes);

    let glyphs = &wf["design"]["glyphs"];
    println!("Glyphs: running={:?} idle={:?}",
        glyphs["running"], glyphs["idle"]);

    println!("\nAll OK — wireframes.json is well-formed.");
}
