//! Generic, data-driven scene renderers. `Kind` maps each scene in `scenes.json`
//! to a polished layout; content is projected/layout-fit at load time so generated
//! copy can't overflow the frame. Every scene is built by pushing *owned* elements
//! into a `Vec<Svgr<'static>>` and interpolating that vector — the idiom the
//! `svgr!` macro supports directly.

use crate::chrome::*;
use crate::spec::{SceneSpec, Theme};
use fframes::{AudioMap, AudioTrack, AudioTimestamp::*, Frame, Svgr, Transform};
use std::sync::Arc;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Title, Bullets, Steps, Stat, Compare, Quote, Flow, Outro, Plain,
}

impl Kind {
    pub fn from_str(s: &str) -> Kind {
        match s {
            "title" => Kind::Title,
            "bullets" => Kind::Bullets,
            "steps" => Kind::Steps,
            "stat" => Kind::Stat,
            "compare" => Kind::Compare,
            "quote" => Kind::Quote,
            "flow" => Kind::Flow,
            "outro" => Kind::Outro,
            _ => Kind::Plain,
        }
    }
    pub fn name(&self) -> &'static str {
        match self {
            Kind::Title => "TITLE",
            Kind::Bullets => "BULLETS",
            Kind::Steps => "STEPS",
            Kind::Stat => "STAT",
            Kind::Compare => "COMPARE",
            Kind::Quote => "QUOTE",
            Kind::Flow => "FLOW",
            Kind::Outro => "OUTRO",
            Kind::Plain => "SCENE",
        }
    }
    pub fn default_sfx(&self) -> Vec<&'static str> {
        match self {
            Kind::Title => vec!["whoosh", "hit"],
            Kind::Steps | Kind::Flow => vec!["click"],
            Kind::Stat => vec!["whoosh_short", "ping"],
            Kind::Compare | Kind::Quote => vec!["whoosh_short"],
            Kind::Bullets => vec!["tick"],
            Kind::Outro => vec!["whoosh", "chime"],
            Kind::Plain => vec!["tick"],
        }
    }
}

const SFX_GAIN: [(&str, f32); 9] = [
    ("whoosh", -14.0), ("whoosh_short", -16.0), ("click", -12.0), ("tick", -16.0),
    ("ping", -15.0), ("hit", -12.0), ("chime", -15.0), ("riser", -16.0), ("data", -16.0),
];

#[derive(Debug)]
pub struct Shot {
    pub kind: Kind,
    pub accent: String,
    pub kicker: String,
    pub label: String,
    pub start: f32,
    pub dur: f32,
    pub heading: String,
    pub items: Vec<String>,
    pub theme: Arc<Theme>,
    sfx: Vec<(String, f32)>,
}

impl Shot {
    pub fn new(spec: SceneSpec, theme: &Arc<Theme>, index: usize, count: usize) -> Shot {
        let kind = Kind::from_str(&spec.kind);
        let accent = spec.accent.clone().unwrap_or_else(|| {
            let pal = if theme.palette.is_empty() { vec!["#4fd8ff".to_string()] } else { theme.palette.clone() };
            pal[index % pal.len()].clone()
        });
        let kicker = if spec.kicker.is_empty() {
            format!("{:02} / {:02}", index + 1, count)
        } else {
            spec.kicker.clone()
        };
        let label = if spec.label.is_empty() { spec.heading.to_uppercase() } else { spec.label.clone() };
        let mut sfx: Vec<(String, f32)> = Vec::new();
        let named: Vec<String> = if spec.sfx.is_empty() {
            kind.default_sfx().into_iter().map(|s| s.to_string()).collect()
        } else {
            spec.sfx.clone()
        };
        for n in named {
            let g = SFX_GAIN.iter().find(|(k, _)| *k == n).map(|(_, g)| *g).unwrap_or(-15.0);
            sfx.push((format!("sfx_{n}.wav"), g));
        }
        Shot {
            kind, accent, kicker, label,
            start: spec.start.unwrap_or(0.0),
            dur: spec.duration.max(0.5),
            heading: spec.heading,
            items: spec.items,
            theme: theme.clone(),
            sfx,
        }
    }

    pub fn audio(&self) -> AudioMap<'_> {
        let mut v: Vec<AudioTrack> = Vec::new();
        if let Some((name, g0)) = self.sfx.first() {
            v.push(AudioTrack::new(name, Second(0.0)..Eof).gain_db(*g0));
        }
        if let Some((name, g)) = self.sfx.get(1) {
            v.push(AudioTrack::new(name, Second(0.7)..Eof).gain_db(*g));
        }
        AudioMap::from(v)
    }

    pub fn render(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        match self.kind {
            Kind::Title => self.title(frame, theme),
            Kind::Bullets => self.bullets(frame, theme),
            Kind::Steps => self.steps(frame, theme),
            Kind::Stat => self.stat(frame, theme),
            Kind::Compare => self.compare(frame, theme),
            Kind::Quote => self.quote(frame, theme),
            Kind::Flow => self.flow(frame, theme),
            Kind::Outro => self.outro(frame, theme),
            Kind::Plain => self.bullets(frame, theme),
        }
    }

    // --- decorative floating rounded cards (Title) ---------------------------
    fn cards(&self, frame: &Frame, theme: &Theme) -> Vec<Svgr<'static>> {
        let t = frame.seconds();
        let pal = if theme.palette.is_empty() { vec!["#4fd8ff".to_string(), "#ffbe5c".into(), "#6fe6a8".into(), "#a78bfa".into()] } else { theme.palette.clone() };
        let cells = [
            (1160.0, 220.0, 300.0, 170.0, 0, 0.70), (1470.0, 180.0, 260.0, 150.0, 1, 0.50),
            (1200.0, 470.0, 250.0, 150.0, 2, 0.44), (1465.0, 440.0, 310.0, 180.0, 3, 0.72),
            (1170.0, 700.0, 290.0, 170.0, 0, 0.40), (1500.0, 690.0, 250.0, 150.0, 2, 0.48),
        ];
        let mut v: Vec<Svgr<'static>> = Vec::new();
        for (i, (x, y, w, h, ci, o)) in cells.iter().enumerate() {
            let float = (t * 0.5 + i as f32 * 1.4).sin() * 9.0;
            let start = 0.12 + i as f32 * 0.06;
            let accent = pal[*ci % pal.len()].clone();
            v.push(fframes::svgr!(<g transform={Transform::translate(0.0, float)} opacity={o * ramp(frame, start)}>
                <rect x={x} y={y} width={w} height={h} rx="22" fill={theme.panel2.clone()} stroke={theme.line.clone()} />
                <circle cx={x + 26.0} cy={y + 32.0} r="5" fill={accent.clone()} />
                <rect x={x + 22.0} y={y + 58.0} width={w * 0.6} height="11" rx="5.5" fill={accent.clone()} fill-opacity="0.6" />
                <rect x={x + 22.0} y={y + 82.0} width={w * 0.42} height="8" rx="4" fill={theme.dim.clone()} fill-opacity="0.4" />
                <rect x={x + 22.0} y={y + 102.0} width={w * 0.5} height="6" rx="3" fill={theme.dim.clone()} fill-opacity="0.22" />
            </g>));
        }
        v
    }

    fn title(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.extend(chrome_heading(frame, theme, &self.heading, 142.0, 360.0, 120.0, -2.0, 950.0, 3));
        if let Some(sub) = self.items.first() {
            v.push(fframes::svgr!(<g opacity={ramp(frame, 0.7)} transform={Transform::translate(0.0, rise(frame, 0.7, 30.0))}>
                <text x="142" y="900" font-family={FONT} font-weight="500" font-size="36" fill={theme.dim.clone()}>{sub.clone()}</text>
            </g>));
        }
        v.extend(self.cards(frame, theme));
        fframes::svgr!(<g>{v}</g>)
    }

    fn bullets(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let n = self.items.len().min(6);
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.extend(chrome_heading(frame, theme, &self.heading, 140.0, 300.0, 62.0, -1.0, 1500.0, 2));
        let y0 = 410.0;
        let step = if n <= 4 { 118.0 } else { 100.0 };
        let t = frame.seconds();
        // which bullet is being spoken right now (rows appear every 0.85s from 0.35)
        let active_i: usize = if t < 0.35 { usize::MAX }
            else { (((t - 0.35) / 0.85).floor() as usize).min(n.saturating_sub(1)) };
        for (i, item) in self.items.iter().take(n).enumerate() {
            let start = 0.35 + i as f32 * 0.85;
            let op = ramp(frame, start);
            let y = y0 + i as f32 * step;
            let active = i == active_i && t >= start;
            let future = t < start;
            let color = if active { self.accent.clone() }
                else if future { theme.faint.clone() }
                else { theme.dim.clone() };
            if active {
                // spotlight the row whose narration is currently being read
                v.push(fframes::svgr!(<rect x="126" y={y - 6.0} width="916" height="56" rx="14" fill={self.accent.clone()} fill-opacity="0.06" stroke={self.accent.clone()} stroke-opacity="0.35" />));
            }
            v.extend(bullet_row(i + 1, item, op, 140.0, y, &color, theme, 8.0));
        }
        // right-side "signal ladder": lights up as each bullet is spoken
        let rail_x = 1520.0f32;
        let top_y = 408.0f32;
        let bot_y = 712.0f32;
        let mut ladder: Vec<Svgr<'static>> = Vec::new();
        ladder.push(fframes::svgr!(<rect x="1290" y="320" width="460" height="480" rx="26" fill={theme.panel.clone()} stroke={theme.line.clone()} />));
        ladder.push(fframes::svgr!(<line x1={rail_x} y1={top_y} x2={rail_x} y2={bot_y} stroke={theme.line.clone()} stroke-width="6" stroke-linecap="round" />));
        for i in 0..n {
            let start = 0.35 + i as f32 * 0.85;
            let op = ramp(frame, start);
            let y = bot_y - (i as f32) * ((bot_y - top_y) / (n as f32 - 1.0).max(1.0));
            let on_now = i == active_i && t >= start;
            let pulse = if on_now { 1.0 } else { 0.55 + 0.35 * (t * 2.0 - i as f32).sin().abs() };
            ladder.push(fframes::svgr!(<g opacity={(op * pulse).min(1.0)}>
                <circle cx={rail_x} cy={y} r="15" fill={self.accent.clone()} fill-opacity="0.2" />
                <circle cx={rail_x} cy={y} r="7" fill={self.accent.clone()} />
            </g>));
            if i < n - 1 {
                let y2 = bot_y - (i as f32 + 1.0) * ((bot_y - top_y) / (n as f32 - 1.0).max(1.0));
                ladder.push(fframes::svgr!(<line x1={rail_x} y1={y} x2={rail_x} y2={y2} stroke={self.accent.clone()} stroke-width="6" stroke-opacity={op} stroke-linecap="round" />));
            }
        }
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.3)}>{ladder}</g>));
        fframes::svgr!(<g>{v}</g>)
    }

    fn steps(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let items: Vec<&String> = self.items.iter().filter(|s| !s.is_empty()).take(5).collect();
        let n = items.len().max(1);
        let gap = 40.0f32;
        let side = 320.0f32;
        let total = side * n as f32 + gap * (n as f32 - 1.0);
        let start_x = 140.0 + (1640.0 - total) / 2.0;
        let cy = 560.0f32;
        let t = frame.seconds();
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.extend(chrome_heading(frame, theme, &self.heading, 140.0, 282.0, 58.0, -1.0, 1500.0, 2));
        for (i, it) in items.iter().enumerate() {
            let start = 0.4 + i as f32 * 0.62;
            let x = start_x + i as f32 * (side + gap);
            let active = t >= start - 0.1;
            let numval = format!("{:02}", i + 1);
            let size = fit_size(it, 0.2, side - 44.0, 3, 28.0);
            let lines = wrap(it, size, 0.2, side - 44.0);
            let mut g: Vec<Svgr<'static>> = Vec::new();
            g.push(fframes::svgr!(<rect x={x} y={cy - 110.0} width={side} height="220" rx="22" fill={theme.panel.clone()} stroke={if active { self.accent.clone() } else { theme.line.clone() }} stroke-opacity={if active {0.8f32} else {1.0f32}} />));
            g.push(fframes::svgr!(<text x={x + 26.0} y={cy - 56.0} font-family={FONT} font-weight="500" font-size="54" letter-spacing="-1" fill={if active { self.accent.clone() } else { theme.faint.clone() }}>{numval}</text>));
            let mut ly = cy - 2.0;
            for ln in &lines {
                g.push(fframes::svgr!(<text x={x + 26.0} y={ly} font-family={FONT} font-weight="500" font-size={size_str(size)} fill={theme.text.clone()}>{ln.clone()}</text>));
                ly += size + 6.0;
            }
            if active && i < n - 1 {
                let px = x + side + 6.0;
                let py = cy + 8.0;
                g.push(fframes::svgr!(<path d={format!("M{px} {py} l24 0 m-14 -10 l20 10 l-20 10")} stroke={theme.dim.clone()} stroke-width="4" fill="none" stroke-opacity="0.6" />));
                // pulse dot traveling along the connector
                let k = (t * 0.9) % 1.0;
                let dx = px + 26.0 * k;
                g.push(fframes::svgr!(<circle cx={dx} cy={py} r="4.5" fill={self.accent.clone()} fill-opacity={(0.25 + 0.7 * k).min(0.95)} />));
            }
            v.push(fframes::svgr!(<g opacity={ramp(frame, start)} transform={Transform::translate(0.0, rise(frame, start, 30.0))}>{g}</g>));
        }
        fframes::svgr!(<g>{v}</g>)
    }

    fn stat(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let t = frame.seconds();
        let p = smooth(clamp01((t - 0.3) / 1.1));
        let (value, suffix): (String, String) = {
            let h = self.heading.trim();
            let mut num = String::new();
            let mut suf = String::new();
            for c in h.chars() {
                if c.is_ascii_digit() || c == '%' || c == '.' || c == '+' {
                    num.push(c);
                } else if !num.is_empty() {
                    suf.push(c);
                }
            }
            if num.is_empty() { (h.to_string(), String::new()) } else { (num, suf) }
        };
        let label_if_num = value.chars().any(|c| c.is_ascii_digit()) && !value.contains('%') && !value.contains('.') && !value.contains('+');
        let displayed: String = if label_if_num {
            match value.parse::<f64>() {
                Ok(f) => format!("{:.0}", f * p as f64),
                Err(_) => value.clone(),
            }
        } else {
            value.clone()
        };
        // width must be computed before the text element is emitted
        let d_w = text_w(&displayed, 128.0, -3.0);

        let cx = 1180.0f32;
        let cy = 470.0f32;
        let r = 170.0f32;
        let ang = std::f32::consts::PI * p;
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.2)}>
            <text x="140" y="560" font-family={FONT} font-weight="500" font-size="128" letter-spacing="-3" fill={self.accent.clone()}>{displayed}</text>
            <text x={140.0 + d_w + 26.0} y="560" font-family={FONT} font-weight="500" font-size="44" fill={theme.text.clone()}>{suffix}</text>
        </g>));
        if let Some(label) = self.items.first() {
            v.push(fframes::svgr!(<g opacity={ramp(frame, 0.55)}>
                <text x="142" y="640" font-family={FONT} font-weight="500" font-size="36" fill={theme.dim.clone()}>{label.clone()}</text>
            </g>));
        }
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.4)}>
            <circle cx={cx} cy={cy} r={r + 26.0} fill="none" stroke={self.accent.clone()} stroke-opacity="0.25" stroke-width="2" stroke-dasharray="6 12" stroke-dashoffset={-(t * 30.0)} />
            <circle cx={cx} cy={cy} r={r} fill="none" stroke={theme.line.clone()} stroke-width="14" />
            <path d={format!("M{} {} A {} {} 0 {} 1 {} {}", cx - r, cy, r, r, if ang <= std::f32::consts::PI { 0 } else { 1 }, cx + r * ang.cos(), cy + r * ang.sin())} fill="none" stroke={self.accent.clone()} stroke-width="14" stroke-linecap="round" />
            <text x={cx} y={cy + 20.0} text-anchor="middle" font-family={FONT} font-weight="500" font-size="44" fill={theme.text.clone()}>{format!("{:.0}%", p * 100.0)}</text>
        </g>));
        for (i, item) in self.items.iter().skip(1).take(3).enumerate() {
            let start = 0.7 + i as f32 * 0.4;
            let y = 720.0 + i as f32 * 56.0;
            let op = ramp(frame, start);
            v.push(fframes::svgr!(<g opacity={op}>
                <circle cx="142" cy={y - 8.0} r="4" fill={self.accent.clone()} />
                <text x="166" y={y} font-family={FONT} font-weight="500" font-size="28" fill={theme.text.clone()}>{item.clone()}</text>
            </g>));
        }
        fframes::svgr!(<g>{v}</g>)
    }

    fn compare(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let t = frame.seconds();
        let lh = self.items.first().cloned().unwrap_or_else(|| "BEFORE".to_string());
        let rh = self.items.get(1).cloned().unwrap_or_else(|| "AFTER".to_string());
        let lpts: Vec<String> = self.items.iter().skip(2).step_by(2).cloned().collect();
        let rpts: Vec<String> = self.items.iter().skip(3).step_by(2).cloned().collect();
        let n = lpts.len().min(rpts.len()).min(5);

        let lc = self.accent.clone();
        let rc = theme.palette.get((theme.palette.iter().position(|c| *c == self.accent).unwrap_or(0) + 1) % theme.palette.len().max(1)).cloned().unwrap_or_else(|| "#ffbe5c".into());

        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.extend(chrome_heading(frame, theme, &self.heading, 140.0, 272.0, 56.0, -1.0, 1500.0, 2));
        let pw = 720.0f32;
        let lx = 140.0f32;
        let rx = 1060.0f32;
        let top = 360.0f32;
        let ph = 470.0f32;
        for (side, (x, header, pts, color)) in [(lx, &lh, &lpts, &lc), (rx, &rh, &rpts, &rc)].into_iter().enumerate() {
            let sbase = 0.15 + side as f32 * 0.12;
            let mut g: Vec<Svgr<'static>> = Vec::new();
            g.push(crate::chrome::panel(x, top, pw, ph, theme));
            g.push(fframes::svgr!(<text x={x + 34.0} y={top + 70.0} font-family={FONT} font-weight="500" font-size="34" letter-spacing="2" fill={theme.text.clone()}>{header.clone()}</text>));
            g.push(fframes::svgr!(<rect x={x + 34.0} y={top + 96.0} width="64" height="5" rx="2.5" fill={color.clone()} />));
            for (i, pt) in pts.iter().take(n).enumerate() {
                let start = sbase + 0.3 + i as f32 * 0.55;
                let y = top + 150.0 + i as f32 * 60.0;
                let op = ramp(frame, start);
                let size = fit_size(pt, 0.3, pw - 88.0, 2, 28.0);
                let lines = wrap(pt, size, 0.3, pw - 88.0);
                let pt_active = t >= start && t < start + 0.55;
                if pt_active {
                    // spotlight the point currently being read
                    g.push(fframes::svgr!(<rect x={x + 26.0} y={y - 20.0} width={pw - 52.0} height="42" rx="10" fill={color.clone()} fill-opacity="0.08" />));
                }
                g.push(fframes::svgr!(<g opacity={op}>
                    <circle cx={x + 40.0} cy={y - 10.0} r="5" fill={color.clone()} />
                    { static_lines(&lines, size, 0.3, x + 62.0, y - size - 2.0, theme.text.as_str()) }
                </g>));
            }
            // a fill bar (with a faint track) grows as this side's points are spoken
            let completed = (0..n).filter(|&k| t >= sbase + 0.3 + k as f32 * 0.55).count() as f32;
            let frac = (completed / n as f32).clamp(0.0, 1.0);
            g.push(fframes::svgr!(<rect x={x + 34.0} y={top + ph - 26.0} width={(pw - 68.0).max(0.0)} height="8" rx="4" fill={theme.line.clone()} fill-opacity="0.5" />));
            g.push(fframes::svgr!(<rect x={x + 34.0} y={top + ph - 26.0} width={(pw - 68.0).max(0.0) * frac} height="8" rx="4" fill={color.clone()} />));
            v.push(fframes::svgr!(<g>{g}</g>));
        }
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.4)} transform={Transform::translate(0.0, rise(frame, 0.4, 24.0))}>
            <circle cx="980" cy="590" r={42.0 + 34.0 * ((t * 0.9) % 1.0)} fill="none" stroke={self.accent.clone()} stroke-opacity={(1.0 - (t * 0.9) % 1.0) * 0.5} />
            <circle cx="980" cy="590" r="42" fill={theme.bg.clone()} stroke={theme.line.clone()} stroke-width="2" />
            <text x="980" y="604" text-anchor="middle" font-family={FONT} font-weight="500" font-size="28" letter-spacing="1" fill={theme.text.clone()}>{"VS"}</text>
        </g>));
        fframes::svgr!(<g>{v}</g>)
    }

    fn quote(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let t = frame.seconds();
        let size = fit_size(&self.heading, 0.0, 1500.0, 4, 60.0);
        let lines = wrap(&self.heading, size, 0.0, 1500.0);
        let lh = size * 1.1;
        let total_lh = lh * lines.len() as f32;
        let y0 = 500.0 - total_lh / 2.0;
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.push(fframes::svgr!(<g transform={Transform::translate(0.0, 10.0 * (t * 0.9).sin())}>
            <text x="140" y="250" font-family={FONT} font-weight="500" font-size="120" fill={self.accent.clone()} opacity="0.9">{"\u{201C}"}</text>
        </g>));
        for (i, ln) in lines.iter().enumerate() {
            let start = 0.2 + i as f32 * 0.18;
            v.push(fframes::svgr!(<g opacity={ramp(frame, start)} transform={Transform::translate(0.0, rise(frame, start, 30.0))}>
                <text x="140" y={y0 + i as f32 * lh} font-family={FONT} font-weight="500" font-size={size_str(size)} fill={theme.text.clone()}>{ln.clone()}</text>
            </g>));
        }
        if let Some(attr) = self.items.first() {
            let ay = y0 + lines.len() as f32 * lh + 52.0;
            v.push(fframes::svgr!(<g opacity={ramp(frame, 0.8)}>
                <rect x="144" y={ay - 28.0} width="64" height="4" rx="2" fill={self.accent.clone()} />
                <text x="234" y={ay} font-family={FONT} font-weight="500" font-size="32" fill={theme.dim.clone()}>{attr.clone()}</text>
            </g>));
        }
        fframes::svgr!(<g>{v}</g>)
    }

    fn flow(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let items: Vec<&String> = self.items.iter().filter(|s| !s.is_empty()).take(5).collect();
        let n = items.len().max(1);
        let bw = (1640.0 - (n as f32 - 1.0) * 54.0) / n as f32;
        let x0 = 140.0f32;
        let y = 520.0f32;
        let t = frame.seconds();
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.extend(chrome_heading(frame, theme, &self.heading, 140.0, 278.0, 56.0, -1.0, 1500.0, 2));
        for (i, it) in items.iter().enumerate() {
            let bx = x0 + i as f32 * (bw + 54.0);
            let start = 0.35 + i as f32 * 0.5;
            let active = t >= start;
            let numval = format!("{:02}", i + 1);
            let size = fit_size(it, 0.2, bw - 56.0, 3, 30.0);
            let lines = wrap(it, size, 0.2, bw - 56.0);
            let mut g: Vec<Svgr<'static>> = Vec::new();
            g.push(fframes::svgr!(<rect x={bx} y={y - 70.0} width={bw} height="140" rx="20" fill={theme.panel.clone()} stroke={if active { self.accent.clone() } else { theme.line.clone() }} stroke-opacity={if active {0.7f32} else {1.0f32}} />));
            g.push(fframes::svgr!(<text x={bx + 22.0} y={y - 22.0} font-family={FONT} font-weight="500" font-size="26" fill={if active { self.accent.clone() } else { theme.faint.clone() }}>{numval}</text>));
            let mut ly = y + 8.0;
            for ln in &lines {
                g.push(fframes::svgr!(<text x={bx + 22.0} y={ly} font-family={FONT} font-weight="500" font-size={size_str(size)} fill={theme.text.clone()}>{ln.clone()}</text>));
                ly += size + 6.0;
            }
            if i < n - 1 {
                let ax = bx + bw + 6.0;
                g.push(fframes::svgr!(<path d={format!("M{ax} {}l40 0m-16 -14l20 14l-20 14", y)} stroke={theme.dim.clone()} stroke-width="4" fill="none" stroke-opacity="0.55" />));
            }
            v.push(fframes::svgr!(<g opacity={ramp(frame, start)}>{g}</g>));
        }
        let span = n as f32 * (bw + 54.0) - 54.0;
        for off in [0.0f32, 0.5] {
            let k = (t * 0.4 + off) % 1.0;
            let px = x0 + span * k;
            v.push(fframes::svgr!(<circle cx={px} cy={y - 70.0} r={4.0 + 3.0 * k} fill={self.accent.clone()} fill-opacity={(0.3 + 0.6 * k).min(0.9)} />));
        }
        fframes::svgr!(<g>{v}</g>)
    }

    fn outro(&self, frame: &Frame, theme: &Theme) -> Svgr<'static> {
        let mut v: Vec<Svgr<'static>> = Vec::new();
        v.push(chapter(&self.kicker, &self.label, &self.accent, theme));
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.05)} transform={Transform::translate(0.0, rise(frame, 0.05, 34.0))}>
            <text x="142" y="340" font-family={FONT} font-weight="500" font-size="26" letter-spacing="6" fill={self.accent.clone()}>{"WRAP-UP"}</text>
        </g>));
        v.extend(chrome_heading(frame, theme, &self.heading, 140.0, 420.0, 96.0, -2.0, 900.0, 3));
        for (i, item) in self.items.iter().take(4).enumerate() {
            let start = 0.5 + i as f32 * 0.5;
            let y = 640.0 + i as f32 * 58.0;
            let op = ramp(frame, start);
            let prog = spring01(frame, start);
            let dx = 26.0 * prog;
            v.push(fframes::svgr!(<g opacity={op} transform={Transform::translate(dx, 0.0)}>
                <circle cx="156" cy={y - 10.0} r="14" fill="none" stroke={self.accent.clone()} stroke-width="3" />
                <path d={format!("M150 {}l5 5l10 -11", y - 14.0)} stroke={self.accent.clone()} stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
                <text x="190" y={y} font-family={FONT} font-weight="500" font-size="30" fill={theme.text.clone()}>{item.clone()}</text>
            </g>));
        }
        for k in [0.0f32, 0.33, 0.66] {
            let pulse = (frame.seconds() * 0.6 + k) % 1.0;
            v.push(fframes::svgr!(<circle cx="1540" cy="620" r={78.0 + 52.0 * pulse} fill="none" stroke={self.accent.clone()} stroke-width="2" stroke-opacity={(1.0 - pulse) * 0.45} />));
        }
        v.push(fframes::svgr!(<g opacity={ramp(frame, 0.4)}>
            <circle cx="1540" cy="620" r="64" fill={self.accent.clone()} />
            <path d="M1524 592l40 28-40 28Z" fill="#04101b" />
        </g>));
        fframes::svgr!(<g>{v}</g>)
    }
}

/// Re-export wrapper so scenes call `chrome::heading` unambiguously (avoids the
/// `panel` name clash that tripped the glob import in `compare`).
fn chrome_heading(
    frame: &Frame, theme: &Theme, text: &str, x: f32, y0: f32,
    size0: f32, spacing: f32, max_w: f32, max_lines: usize,
) -> Vec<Svgr<'static>> {
    crate::chrome::heading(frame, theme, text, x, y0, size0, spacing, max_w, max_lines)
}