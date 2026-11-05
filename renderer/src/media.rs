//! Runtime media provider: loads wav audio and fonts from the job directory.
//! Because media is resolved at render time, this one prebuilt binary can render
//! any number of different videos without recompiling.

use crate::spec::Reel;
use fframes::{AudioData, DynamicMediaProvider, RawFontData};
use std::borrow::Cow;
use std::fs;
use std::path::Path;
use std::sync::Arc;

fn decode_wav(path: &Path) -> (Vec<f32>, Option<Vec<f32>>, u32) {
    let mut rdr = hound::WavReader::open(path).expect("open wav");
    let spec = rdr.spec();
    let sr = spec.sample_rate;
    let ch = spec.channels as usize;
    let mut frames: Vec<f32> = match spec.sample_format {
        hound::SampleFormat::Int => match spec.bits_per_sample {
            16 => rdr.samples::<i16>().map(|s| s.unwrap() as f32 / 32768.0).collect(),
            24 | 32 => rdr
                .samples::<i32>()
                .map(|s| s.unwrap() as f32 / 2147483648.0)
                .collect(),
            b => panic!("unsupported int wav bit depth {b}"),
        },
        hound::SampleFormat::Float => rdr.samples::<f32>().map(|s| s.unwrap()).collect(),
    };
    if ch > 1 {
        // Interleaved L/R -> separate L and R vectors (drop any extra channels).
        let total = frames.len() / ch * 2;
        let mut left = Vec::with_capacity(total / 2);
        let mut right = Vec::with_capacity(total / 2);
        let mut i = 0;
        while frames.len() >= ch && i + 1 < frames.len() {
            left.push(frames[i]);
            right.push(frames[i + 1]);
            i += ch;
        }
        (left, Some(right), sr)
    } else {
        (frames, None, sr)
    }
}

/// Build a media provider from the files already staged in `dir`.
pub fn build(dir: &Path, _reel: &Reel) -> DynamicMediaProvider<'static> {
    let mut p = DynamicMediaProvider::default();

    // Audio: voice + music (optional) + generated sfx (`sfx_*.wav`).
    p = add_audio(p, "voice.wav", dir);
    p = add_audio(p, "music.wav", dir);
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with("sfx_") && name.ends_with(".wav") {
                p = add_audio(p, &name, dir);
            }
        }
    }

    // Fonts: register every ttf/otf in the directory (the parsed family name is
    // what `<text font-family="DM Sans">` resolves against).
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name.ends_with(".ttf") || name.ends_with(".otf") {
                if let Ok(bytes) = fs::read(e.path()) {
                    p = p.with_font(RawFontData {
                        file_name: name,
                        data: Arc::new(bytes),
                    });
                }
            }
        }
    }

    p
}

fn add_audio<'a>(mut p: DynamicMediaProvider<'a>, asset: &str, dir: &Path) -> DynamicMediaProvider<'a> {
    let path = dir.join(asset);
    if !path.exists() {
        return p;
    }
    let (l, r, sr) = decode_wav(&path);
    p.with_audio(
        asset.to_string(),
        AudioData::Preloaded(fframes::media::PreloadedAudioData {
            samples: Cow::Owned(l),
            sample_rate: sr,
            right: r.map(Cow::Owned),
        }),
    )
}