//! Shared drawing helpers: text metrics/wrapping, easing, chrome, progress and
//! word-synced captions. Kept generic so one binary can render any job.

use crate::spec::{Subs, Theme, Word};
use fframes::{
    AnimateRuntimeInput, Frame, Svgr, Transform,
    animation::{AnimationRuntime, Easing},
};
use std::sync::LazyLock;

pub const W: usize = 1920;
pub const H: usize = 1080;
pub const FPS: usize = 30;
pub const FONT: &str = "DM Sans";

pub fn clamp01(x: f32) -> f32 {
    x.clamp(0.0, 1.0)
}
pub fn smooth(x: f32) -> f32 {
    let x = clamp01(x);
    x * x * (3.0 - 2.0 * x)
}

static EASE: LazyLock<AnimationRuntime> =
    LazyLock::new(|| AnimationRuntime::new(0.85, &Easing::EaseOut));
static SPRING: LazyLock<AnimationRuntime> =
    LazyLock::new(|| AnimationRuntime::new(3.0, &Easing::Spring { mass: 1.0, stiffness: 180.0, damping: 20.0 }));

pub fn ramp(frame: &Frame, start: f32) -> f32 {
    clamp01(frame.animate_runtime(AnimateRuntimeInput {
        on_second: start, from: 0.0, to: 1.0, animation_runtime: &EASE,
    }))
}
pub fn rise(frame: &Frame, start: f32, dist: f32) -> f32 {
    dist * (1.0 - spring01(frame, start))
}
pub fn spring01(frame: &Frame, start: f32) -> f32 {
    frame.animate_runtime(AnimateRuntimeInput {
        on_second: start, from: 0.0, to: 1.0, animation_runtime: &SPRING,
    })
}

/// Fixed font sizes as static strings so `svgr!` emits literal attributes
/// (shrink-to-fit picks the nearest bucket).
const SIZES: [(&str, f32); 24] = [
    ("20",20.0),("22",22.0),("24",24.0),("26",26.0),("28",28.0),("30",30.0),
    ("32",32.0),("34",34.0),("36",36.0),("38",38.0),("40",40.0),("44",44.0),
    ("48",48.0),("52",52.0),("56",56.0),("60",60.0),("66",66.0),("72",72.0),
    ("80",80.0),("88",88.0),("96",96.0),("112",112.0),("128",128.0),("150",150.0),
];
pub fn size_str(px: f32) -> &'static str {
    let mut best: &str = "128";
    let mut bd = (px - 128.0).abs();
    for (s, v) in SIZES {
        let d = (px - v).abs();
        if d < bd {
            bd = d;
            best = s;
        }
    }
    best
}

fn cw(c: char) -> f32 {
    match c {
        ' ' => 0.33,
        'M' | 'W' | 'm' | 'w' | '@' => 0.92,
        'I' | 'i' | 'l' | 'j' | 't' | 'f' | '1' | '.' | ',' | '!' | '\'' | ':' | ';' | '|' => 0.34,
        '0'..='9' => 0.58,
        'A'..='Z' => 0.66,
        'a'..='z' => 0.52,
        _ => 0.55,
    }
}
pub fn text_w(s: &str, size: f32, spacing: f32) -> f32 {
    let chars: Vec<char> = s.chars().collect();
    let sum: f32 = chars.iter().map(|&c| cw(c) * size).sum();
    sum + spacing * (chars.len() as f32 - 1.0).max(0.0)
}
pub fn wrap(s: &str, size: f32, spacing: f32, max_w: f32) -> Vec<String> {
    let words: Vec<&str> = s.split_whitespace().collect();
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cur_w = 0.0;
    for w in words {
        let ww = text_w(w, size, 0.0) + if cur.is_empty() { 0.0 } else { text_w(" ", size, 0.0) };
        if !cur.is_empty() && cur_w + ww > max_w {
            lines.push(cur.clone());
            cur = String::new();
            cur_w = 0.0;
        }
        if cur.is_empty() {
            cur.push_str(w);
            cur_w = text_w(w, size, 0.0);
        } else {
            cur.push(' ');
            cur.push_str(w);
            cur_w += ww;
        }
    }
    if !cur.is_empty() {
        lines.push(cur);
    }
    lines
}
pub fn fit_size(s: &str, spacing: f32, max_w: f32, max_lines: usize, start_px: f32) -> f32 {
    let mut px = start_px;
    loop {
        let lines = wrap(s, px, spacing, max_w);
        if lines.len() <= max_lines || px <= 26.0 {
            return px;
        }
        px = (px * 0.88).round();
    }
}

/// Accent dot + label on the left (scene counters removed by design).
pub fn chapter(_kicker: &str, label: &str, accent: &str, th: &Theme) -> Svgr<'static> {
    fframes::svgr!(<g>
        <circle cx="146" cy="151" r="5" fill={accent.to_string()} />
        <text x="166" y="160" font-family={FONT} font-weight="500" font-size="23" letter-spacing="4" fill={th.dim.clone()}>{label.to_string()}</text>
    </g>)
}

/// Backdrop shared by all scenes: base rect, radial glow, faint grid.
pub fn background(th: &Theme) -> Svgr<'static> {
    let mut grid = String::new();
    let mut x = 0;
    while x <= 1920 {
        grid.push_str(&format!("M{x} 0V1080"));
        x += 80;
    }
    let mut y = 0;
    while y <= 1080 {
        grid.push_str(&format!("M0 {y}H1920"));
        y += 80;
    }
    fframes::svgr!(
        <svg xmlns="http://www.w3.org/2000/svg" width={W} height={H} viewBox="0 0 1920 1080">
            <rect x="0" y="0" width="1920" height="1080" fill={th.bg.clone()} />
            <radialGradient id="bgGlow" cx="72%" cy="26%" r="78%">
                <stop offset="0%" stop-color={th.panel2.clone()} stop-opacity="0.9" />
                <stop offset="100%" stop-color={th.bg.clone()} stop-opacity="0.0" />
            </radialGradient>
            <rect x="0" y="0" width="1920" height="1080" fill="url(#bgGlow)" />
            <g stroke={th.line.clone()} stroke-opacity="0.35" stroke-width="1">
                <path d={grid} />
            </g>
        </svg>
    )
}

/// Ambient motion layer behind every scene: slow drifting color orbs + floating
/// particles. Adds life without competing with the content.
pub fn ambient(frame: &Frame, th: &Theme) -> Svgr<'static> {
    let t = frame.seconds();
    let pal = if th.palette.is_empty() {
        vec!["#4fd8ff".to_string(), "#a78bfa".into(), "#6fe6a8".into()]
    } else {
        th.palette.clone()
    };
    let mut v: Vec<Svgr<'static>> = Vec::new();
    for (i, c) in pal.iter().take(3).enumerate() {
        let id = format!("orb{}", i);
        v.push(fframes::svgr!(<radialGradient id={id}>
            <stop offset="0%" stop-color={c.clone()} stop-opacity="0.26" />
            <stop offset="100%" stop-color={c.clone()} stop-opacity="0.0" />
        </radialGradient>));
    }
    let orbs = [
        (1400.0, 320.0, 520.0, 0usize, 0.16f32),
        (360.0, 850.0, 480.0, 1, 0.20),
        (980.0, 150.0, 380.0, 2, 0.12),
    ];
    for (bx, by, r, ci, sp) in orbs {
        let x = bx + 170.0 * (t * sp).sin();
        let y = by + 120.0 * (t * sp * 1.3).cos();
        let fill = format!("url(#orb{})", ci);
        v.push(fframes::svgr!(<circle cx={x} cy={y} r={r} fill={fill} />));
    }
    for i in 0..26 {
        let x = (i as f32 * 173.0 + 60.0) % 1920.0;
        let y = 1180.0 - ((t * 26.0 + i as f32 * 121.0) % 1320.0);
        let rr = 2.0 + (i % 4) as f32;
        let op = 0.18 + 0.22 * (t * 0.9 + i as f32).sin().abs();
        let c = pal[(i + 1) % pal.len()].clone();
        v.push(fframes::svgr!(<circle cx={x} cy={y} r={rr} fill={c} fill-opacity={op} />));
    }
    fframes::svgr!(<g>{v}</g>)
}

/// Rounded panel with subtle border (used by compare scenes).
pub fn panel(x: f32, y: f32, w: f32, h: f32, th: &Theme) -> Svgr<'static> {
    fframes::svgr!(<rect x={x} y={y} width={w} height={h} rx="22" fill={th.panel.clone()} stroke={th.line.clone()} />)
}

// ---------------------------------------------------------------------------
// Captions: word-synced karaoke bar driven by whisper alignment.
// ---------------------------------------------------------------------------

pub struct Cue {
    pub words: Vec<Word>,
    pub s: f32,
    pub e: f32,
}

pub fn build_cues(sub: &Subs, max_words: usize) -> Vec<Cue> {
    let words = &sub.words;
    let mut cues = Vec::new();
    let mut i = 0;
    while i < words.len() {
        let take = max_words.min(words.len() - i);
        let chunk: Vec<Word> = words[i..i + take].to_vec();
        let s = chunk.first().map(|w| w.s).unwrap_or(0.0);
        let e = chunk.last().map(|w| w.e).unwrap_or(0.0);
        cues.push(Cue { words: chunk, s, e });
        i += take;
    }
    cues
}

pub fn caption(frame: &Frame, cues: &[Cue], accent: &str, th: &Theme) -> Option<Svgr<'static>> {
    let t = frame.seconds();
    let cue = cues.iter().find(|c| t >= c.s - 0.05 && t <= c.e + 0.35)?;
    let fade = smooth(clamp01((t - (cue.s - 0.05)) / 0.12)) * smooth(clamp01((cue.e + 0.35 - t) / 0.22));
    if fade <= 0.01 {
        return None;
    }
    let bw = text_w(&cue.words.iter().map(|w| w.w.clone()).collect::<Vec<_>>().join(" "), 30.0, 0.0) + 120.0;
    let bx = (1920.0 - bw) / 2.0;
    let by = 924.0;
    let mut elems: Vec<Svgr<'static>> = Vec::new();
    elems.push(fframes::svgr!(<rect x={bx} y={by - 8.0} width={bw} height="52" rx="26" fill={th.bg.clone()} fill-opacity="0.72" stroke={th.line.clone()} />));
    let mut cx = bx + 60.0;
    for w in &cue.words {
        let active = t >= w.s && t < w.e;
        let fill = if active { accent.to_string() } else { th.text.clone() };
        elems.push(fframes::svgr!(<text x={cx} y={by + 31.0} font-family={FONT} font-weight="500" font-size="30" fill={fill}>{w.w.clone()}</text>));
        cx += text_w(&w.w, 30.0, 0.0) + text_w(" ", 30.0, 0.0);
    }
    elems.push(fframes::svgr!(<g opacity={fade}></g>));
    Some(fframes::svgr!(<g opacity={fade}>{elems}</g>))
}

/// Convenience: an arranged bullet row (number chip + wrapped text).
pub fn bullet_row(n: usize, t: &str, op: f32, x: f32, y: f32, accent: &str, th: &Theme, line_h: f32) -> Vec<Svgr<'static>> {
    let mut v: Vec<Svgr<'static>> = Vec::new();
    let num = format!("{:02}", n);
    v.push(fframes::svgr!(<g opacity={op}>
        <rect x={x} y={y} width="46" height="46" rx="13" fill={accent.to_string()} fill-opacity="0.14" stroke={accent.to_string()} stroke-opacity="0.5" />
        <text x={x + 23.0} y={y + 31.0} text-anchor="middle" font-family={FONT} font-weight="500" font-size="22" fill={accent.to_string()}>{num}</text>
    </g>));
    let size = fit_size(t, 0.5, 820.0, 2, 34.0);
    let lines = wrap(t, size, 0.5, 820.0);
    let mut ly = y + 31.0;
    for ln in &lines {
        v.push(fframes::svgr!(<g opacity={op}>
            <text x={x + 70.0} y={ly} font-family={FONT} font-weight="500" font-size={size_str(size)} fill={th.text.clone()}>{ln.clone()}</text>
        </g>));
        ly += size + line_h;
    }
    v
}

/// Owned heading block: auto-fits and wraps, returns one animated line element each.
/// All text is cloned into owned Strings so the returned elements are `'static`.
pub fn heading(
    frame: &Frame, theme: &Theme, text: &str, x: f32, y0: f32,
    size0: f32, spacing: f32, max_w: f32, max_lines: usize,
) -> Vec<Svgr<'static>> {
    let size = fit_size(text, spacing, max_w, max_lines, size0);
    let lines = wrap(text, size, spacing, max_w);
    let mut v: Vec<Svgr<'static>> = Vec::new();
    for (i, ln) in lines.iter().enumerate() {
        let start = 0.0 + i as f32 * 0.12;
        let y = y0 + i as f32 * size;
        let r = ramp(frame, start);
        let slid = 30.0 * (1.0 - r);
        let dy = rise(frame, start, 32.0);
        v.push(fframes::svgr!(<g opacity={r} transform={Transform::translate(slid, dy)}>
            <text x={x} y={y} font-family={FONT} font-weight="500" font-size={size_str(size)} letter-spacing={spacing} fill={theme.text.clone()}>{ln.clone()}</text>
        </g>));
    }
    v
}

/// Owned static line block (no animation) used inside panels.
pub fn static_lines(lines: &[String], size: f32, spacing: f32, x: f32, y0: f32, color: &str) -> Vec<Svgr<'static>> {
    let mut v: Vec<Svgr<'static>> = Vec::new();
    for (i, ln) in lines.iter().enumerate() {
        v.push(fframes::svgr!(<text x={x} y={y0 + i as f32 * size} font-family={FONT} font-weight="500" font-size={size_str(size)} letter-spacing={spacing} fill={color.to_string()}>{ln.clone()}</text>));
    }
    v
}