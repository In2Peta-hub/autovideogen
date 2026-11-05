//! `reel-renderer --job <dir> render` — renders the timestamped job in <dir>
//! (scenes.json + voice.wav + optional music.wav + sfx_*.wav + fonts) to out.mp4.
//! Media is resolved at runtime, so this binary never needs a rebuild per video.

use fframes::{EncoderOptions, RenderOptions, YuvMatrix, cli};
use fframes_skia_renderer::{
    SkiaFFramesRenderer, SkiaPipelineConcurrencyPolicy, SkiaPipelineConfig,
    metal::SkiaMetalCtx,
};
use reel_renderer::{ReelVideo, chrome::{W, H}, media, spec};
use fframes::cli::clap;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Debug, clap::Args)]
struct VideoArgs {
    /// Directory containing scenes.json, voice.wav, music.wav, sfx_*.wav and fonts.
    /// Optional at the clap level so fframes' subcommand re-parse stays green; enforced in main.
    #[arg(long, global = true)]
    job: Option<PathBuf>,
}

fn main() -> ExitCode {
    let args = cli::parse::<VideoArgs>();
    let job: PathBuf = match args.app.job.clone() {
        Some(j) => j,
        None => {
            eprintln!("error: --job <dir> is required (a directory with scenes.json + audio)");
            return ExitCode::from(2);
        }
    };

    let reel = spec::load(&job);
    let provider = media::build(&job, &reel);
    let video = ReelVideo::build(reel);

    // Keep encoder temp artifacts inside the job dir.
    let tmp = job.join("tmp");
    std::fs::create_dir_all(&tmp).ok();

    let gpu = SkiaMetalCtx::new(W, H).expect("GPU context");
    let opts = RenderOptions {
        media: Some(&provider),
        video_encoder_options: EncoderOptions {
            preferred_encoder: Some("h264_videotoolbox"),
            bitrate: Some(14_000_000),
            color_matrix: YuvMatrix::Bt709,
            ..Default::default()
        },
        tmp_files_directory: Some(&tmp),
        ..Default::default()
    };

    cli::new(&video, opts)
        .args(args)
        .default_output(job.join("out.mp4"))
        .backend(
            SkiaFFramesRenderer::new_metal(
                &gpu,
                SkiaPipelineConfig {
                    concurrency_policy: SkiaPipelineConcurrencyPolicy::MaxPerformance,
                    ..Default::default()
                },
            )
            .expect("skia renderer"),
        )
        .preview(fframes_native_player::cli_preview)
        .run()
}