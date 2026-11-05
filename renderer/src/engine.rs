//! The video: assembles scenes from the timestamped spec, mixes audio, and
//! applies global chrome (progress bar + word-synced captions).

pub mod chrome;
pub mod media;
pub mod scenes;
pub mod spec;

use chrome::*;
use fframes::{
    AudioMap, AudioTrack, AudioTimestamp::*, Color, Duration, Frame, FFramesContext, Scene,
    Scenes, Svgr, Transform, Video,
};
use scenes::Shot;
use spec::{Reel, Theme};
use std::sync::Arc;

pub struct ReelVideo {
    pub title: String,
    theme: Arc<Theme>,
    pub shots: Vec<Shot>,
    pub total: f32,
    pub cues: Vec<Cue>,
    pub has_music: bool,
}

impl ReelVideo {
    pub fn build(spec: Reel) -> ReelVideo {
        let theme = Arc::new(spec.theme.clone());
        let count = spec.scenes.len();
        let shots: Vec<Shot> = spec
            .scenes
            .into_iter()
            .enumerate()
            .map(|(i, s)| Shot::new(s, &theme, i, count))
            .collect();
        let total = spec
            .total
            .unwrap_or_else(|| shots.iter().map(|s| s.dur).sum());
        let cues = spec
            .subtitles
            .as_ref()
            .map(|sub| chrome::build_cues(sub, 7))
            .unwrap_or_default();
        ReelVideo {
            title: spec.title,
            theme,
            shots,
            total,
            cues,
            has_music: spec.music.is_some(),
        }
    }
}

impl Video for ReelVideo {
    const FPS: usize = chrome::FPS;
    const WIDTH: usize = chrome::W;
    const HEIGHT: usize = chrome::H;
    const BACKGROUND_COLOR: Color = Color::hex("#000000");

    fn duration(&self) -> Duration<'_> {
        Duration::Seconds(self.total)
    }

    fn audio(&self) -> AudioMap<'_> {
        let mut v: Vec<AudioTrack> = vec![AudioTrack::new("voice.wav", Second(0.0)..Eof).voice()];
        if self.has_music {
            v.push(
                AudioTrack::new("music.wav", Second(0.0)..Eof)
                    .gain_db(-5.0)
                    .fade_in(2.0)
                    .fade_out(3.0)
                    .duck_under_voice(),
            );
        }
        AudioMap::from(v)
    }

    fn define_scenes(&self) -> Scenes<'_> {
        let scenes: Vec<&dyn Scene> = self.shots.iter().map(|s| s as &dyn Scene).collect();
        Scenes::from(scenes)
    }

fn render_frame<'a>(&'a self, frame: Frame, ctx: &FFramesContext<'a, '_>) -> Svgr<'a> {
        let accent = &self.theme.accent;
        let sub = chrome::caption(&frame, &self.cues, accent, &self.theme);
        // Continuous handheld drift + breathing zoom so the whole frame is never static.
        let gt = frame.seconds();
        let dx = 14.0 * (gt * 0.45).sin();
        let dy = 11.0 * (gt * 0.32).cos();
        let zoom = 1.0 + 0.018 * (gt * 0.38).sin();
        let bg: Vec<Svgr<'static>> = vec![
            chrome::background(&self.theme),
            chrome::ambient(&frame, &self.theme),
        ];
        fframes::svgr!(<svg xmlns="http://www.w3.org/2000/svg" width={W} height={H} viewBox="0 0 1920 1080">
            <g transform={Transform::translate(dx, dy)}>
                <g transform={Transform::scale(zoom)}>
                    {bg}
                    {ctx.render_scenes(&frame)}
                </g>
            </g>
            { match sub { Some(c) => c, None => fframes::svgr!(<g></g>) } }
            <text x="1780" y="160" text-anchor="end" font-family="DM Sans" font-weight="500" font-size="20" fill={self.theme.faint.clone()} opacity="0.7">{self.title.as_str()}</text>
        </svg>)
    }
}

impl Scene for Shot {
    fn duration(&self) -> Duration<'_> {
        Duration::Seconds(self.dur)
    }
    fn name(&self) -> &'static str {
        self.kind.name()
    }
    fn audio(&self) -> AudioMap<'_> {
        Shot::audio(self)
    }
    fn render_frame<'a>(&'a self, frame: Frame, _ctx: &FFramesContext<'a, '_>) -> Svgr<'a> {
        Shot::render(self, &frame, &self.theme)
    }
}