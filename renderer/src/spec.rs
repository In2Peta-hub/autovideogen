//! Loader + serde types for the timestamped job contract (`scenes.json`).
//!
//! This is the single source of truth a video job is built from: a scene list
//! with absolute `start` times (hence timestamps) and per-scene narration that
//! the backend TTS turns into `voice.wav`, which is then aligned word-by-word
//! into `subtitles`. Timestamps make audio/video sync trivial: the renderer just
//! plays each scene at its `start` and draws the caption for the current time.

use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Deserialize, Clone, Debug)]
pub struct Reel {
    pub title: String,
    #[serde(default)]
    pub total: Option<f32>,
    pub theme: Theme,
    /// Optional music bed asset name (e.g. "music.wav"). When absent no music track is mixed.
    #[serde(default)]
    pub music: Option<String>,
    pub scenes: Vec<SceneSpec>,
    #[serde(default)]
    pub subtitles: Option<Subs>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct Theme {
    pub bg: String,
    #[serde(rename = "panel")]
    pub panel: String,
    pub panel2: String,
    pub line: String,
    pub text: String,
    pub dim: String,
    pub faint: String,
    pub accent: String,
    #[serde(default)]
    pub palette: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct SceneSpec {
    pub id: String,
    pub kind: String,
    #[serde(default)]
    pub kicker: String,
    #[serde(default)]
    pub label: String,
    /// Absolute start time in seconds on the master timeline.
    #[serde(default)]
    pub start: Option<f32>,
    /// Scene length in seconds.
    pub duration: f32,
    #[serde(default)]
    pub heading: String,
    #[serde(default)]
    pub items: Vec<String>,
    #[serde(default)]
    pub narration: String,
    #[serde(default)]
    pub accent: Option<String>,
    #[serde(default)]
    pub sfx: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct Subs {
    pub words: Vec<Word>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct Word {
    pub w: String,
    pub s: f32,
    pub e: f32,
}

pub fn load(dir: &Path) -> Reel {
    let text = fs::read_to_string(dir.join("scenes.json")).expect("read scenes.json");
    serde_json::from_str(&text).expect("parse scenes.json")
}