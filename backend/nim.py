"""OpenAI-compatible chat-responses client.

The API key lives **server-side** (set by the admin), so the frontend never sends
or stores a key. Default provider is In2Peta-1 (DeepSeek V4.1 Flash); any
OpenAI-compatible base_url/model can be selected from the frontend.
"""

from __future__ import annotations

import json
import os
import re

import httpx

# Server-side key. Override without editing code:
#   export REELFORGE_API_KEY=sk-...
DEFAULT_API_KEY = os.environ.get("REELFORGE_API_KEY") or os.environ.get("IN2PETA_KEY") or "sk-Lk3l_CsjtvLB7e8tlLaatA"

DEFAULT_MODEL = "deepseek-v4p1-flash"
DEFAULT_BASE = "https://code.in2peta.com/v1"

# Provider presets so the app stays pluggable between gateways and models.
PROVIDERS = {
    "in2peta-1": {
        "name": "In2Peta-1 (DeepSeek V4.1 Flash)",
        "base": "https://code.in2peta.com/v1",
        "model": "deepseek-v4p1-flash",
    },
    "nvidia": {
        "name": "NVIDIA NIM (MiniMax M3.1 Flash)",
        "base": "https://integrate.api.nvidia.com/v1",
        "model": "minimaxai/minimax-m3.1-flash",
    },
}


def resolve_provider(provider: str | None, model: str | None, base_url: str | None):
    """Pick (model, base_url). A named provider sets defaults; explicit fields override."""
    if provider in PROVIDERS:
        p = PROVIDERS[provider]
        m = model or p["model"]
        b = base_url or p["base"]
        return m, b
    return (model or DEFAULT_MODEL), (base_url or DEFAULT_BASE)


class NimError(Exception):
    pass


def _extract_json(text: str | None):
    text = (text or "").strip()
    if not text:
        raise NimError("empty LLM response")
    # strip markdown fences the model may wrap the object in
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Reasoning models sometimes emit prose before/after the JSON; pull out the
    # first balanced { ... } object.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        end = -1
        j = start
        while j < len(text):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        start = text.find("{", start + 1)
    raise NimError(f"model did not return JSON: {text[:300]}")


def _chat(
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE,
    temperature: float = 0.4,
    max_tokens: int = 4096,
    timeout: float = 240.0,
    api_key: str | None = None,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key or DEFAULT_API_KEY}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise NimError(f"request failed: {e}") from e
    if r.status_code != 200:
        raise NimError(
            f"HTTP {r.status_code} from {url} — {r.text[:300]}. "
            f"If this is 404, the model '{model}' is probably not in the NIM catalog for "
            f"this key; try a model id that is (or edit the Model field). "
            f"If 401/403, check the API key."
        )
    data = r.json()
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content")
        # Reasoning models can return content as null when the chain-of-thought ate
        # the token budget; fall back to reasoning_content so we still get an answer.
        if not content:
            content = msg.get("reasoning_content")
        return (content or "").strip()
    except (KeyError, IndexError) as e:
        raise NimError(f"unexpected LLM response: {str(data)[:300]}") from e


SCENE_SCHEMA = (
    "Output STRICTLY a single JSON object (no prose, no markdown) with this shape:\n"
    "{\n"
    '  "title": "<short title>",\n'
    '  "scenes": [\n'
    "    {\n"
    '      "kind": "title|bullets|steps|stat|compare|quote|flow|outro",\n'
    '      "heading": "<up to 6 words>",\n'
    '      "items": ["<one short phrase, under 9 words>", ... (2-6 items)],\n'
    '      "narration": "<spoken prose, 12-45 words. KEEP the scene\'s most concrete facts from the '
    'source — exact names, numbers, mechanisms, trade-offs. Never flatten them into vague summaries. '
    'Omit the least important detail, not the essence.">\n'
    "    }\n"
    "  ]\n"
    "}\n"
)


def _est_total(scenes: list[dict]) -> float:
    """Local duration estimate (same model as script.py) to guide duration correction."""
    t = 0.0
    for sc in scenes:
        d = 0.35 + max(1, len((sc.get("narration") or "").split())) / 2.55
        t += d + 0.22
    return t - 0.22


def _words(scenes: list[dict]) -> int:
    return sum(max(1, len((sc.get("narration") or "").split())) for sc in scenes)


def _needed_words(target: float, n_scenes: int) -> int:
    # est_total = n*0.35 + W/2.55 + (n-1)*0.22  -> solve for W to hit target
    overhead = n_scenes * 0.35 + (n_scenes - 1) * 0.22
    return max(6, int((target - overhead) * 2.55))


def _adjust_lengths(scenes: list[dict], target: float, model: str, base_url: str) -> list[dict]:
    """Best-effort LLM pass: expand/condense narration (by word count) to hit `target` seconds."""
    cur = _est_total(scenes)
    if abs(cur - target) / max(target, 1.0) < 0.10:
        return scenes
    cur_w = _words(scenes)
    need_w = _needed_words(target, len(scenes))
    short = cur < target
    verb = (
        f"The script is too SHORT (~{cur_w} words) for the target — EXPAND the 'narration' fields "
        f"to about {need_w} words total (add relevant spoken sentences; extra concrete detail)."
        if short else
        f"The script is too LONG (~{cur_w} words) for the target — CONDENSE the 'narration' fields "
        f"to about {need_w} words total. Cut filler and generic phrasing first; KEEP the most specific "
        f"facts and numbers."
    )
    user = (
        f"The video must be ~{target:.0f} seconds long, which needs roughly {need_w} spoken words "
        f"across {len(scenes)} scenes. {verb} "
        "Edit only the 'narration' fields, aiming each one near its fair share of the total. "
        "Keep 'kind'/'heading'/'items' otherwise unchanged. "
        "Output ONLY valid JSON (no reasoning, no markdown): {\"scenes\": [ ... ]} with narration adjusted."
        "\n\n" + json.dumps(scenes, ensure_ascii=False)
    )
    try:
        content = _chat([{"role": "system", "content":
            "You are a precise video script editor that only outputs valid JSON. "
            "You set each scene's 'narration' word-count so the whole script hits the stated word total."},
            {"role": "user", "content": user}], model, base_url, max_tokens=8192)
        obj = _extract_json(content)
    except NimError:
        return scenes
    if isinstance(obj, dict) and "scenes" in obj:
        obj = obj["scenes"]
    if not isinstance(obj, list) or not obj:
        return scenes
    merged = []
    for i, sc in enumerate(scenes):
        news = obj[i] if i < len(obj) else {}
        out = dict(sc)
        if news.get("narration"):
            out["narration"] = news["narration"]
        for k in ("items", "heading"):
            if news.get(k):
                out[k] = news[k]
        merged.append(out)
    return merged


def generate_writeup(
    idea: str,
    style: str = "technical",
    research: list[tuple[str, str]] | None = None,
    target_seconds: int = 120,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE,
) -> tuple[str, int]:
    """Turn a rough idea + optional web research into a detailed markdown source text.

    Returns (writeup_text, words). The writeup is what the user validates, then feeds
    to `generate_script` as `source_text`.
    """
    style_desc = {
        "technical": "a deep technical writeup: components, protocols, data flow, trade-offs and concrete details, structured with markdown headings and bullet lists",
        "architectural": "a system-architecture writeup: components, layering, interfaces, data/info flow and interactions between parts",
        "educational": "a clear teaching piece for a motivated beginner-to-intermediate reader, with analogies and worked explanation",
        "marketing": "an engaging, benefits-and-outcomes driven writeup with a persuasive edge",
        "storytelling": "a narrative, analogy-heavy writeup that explains through a story",
    }.get(style, "a clear, well-structured, informative writeup")

    if research:
        research_block = "\n".join(f"- ({label}) {text}" for label, text in research)
        research_block = ("WEB RESEARCH (summarise, cite the source label inline, do not invent beyond it):\n"
                          + research_block)
    else:
        research_block = ("(No live web research was fetched. Build the writeup from your own knowledge; "
                          "state clearly where detail is uncertain.)")

    target_words = max(400, min(1500, int(target_seconds * 3)))
    system = (
        "You are an expert research writer. From a rough idea you produce a detailed, accurate, "
        "well-structured markdown writeup that can later be turned into a narrated explainer video. "
        "Be concrete and specific. Use markdown headings, bullet lists and short sections. "
        "Ground everything in the provided web research; never fabricate numbers, dates or quotes. "
        "Return ONLY the markdown writeup — no preamble, no closing commentary."
    )
    user = (
        f"TOPIC / IDEA:\n{idea}\n\n"
        f"STYLE: {style_desc}.\n"
        f"TARGET: ~{target_words} words.\n\n"
        f"{research_block}\n\n"
        "Write the detailed markdown source text now."
    )
    content = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                     model, base_url, max_tokens=8192)
    text = content.strip()
    return text, len(text.split())


def generate_script(
    source_text: str = "",
    prompt: str | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE,
    target_seconds: int = 120,
    tone: str = "clear, engaging, neutral-explain",
    voice_lang: str | None = None,
) -> dict:
    """Build a timestamped scene script from a natural-language prompt and/or source text.

    `prompt` is the everyday request ("explain how YouTube works in ~300s"); `source_text`
    is optional reference material the model can enrich from. Output is tuned to `target_seconds`.
    """
    n_scenes = max(6, min(16, round(target_seconds / 9)))
    word_budget = _needed_words(target_seconds, n_scenes)
    lang_note = f"\nLanguage for narration: {voice_lang}." if voice_lang else ""
    system = (
        "You are a scriptwriter and motion-designer for faceless narrated videos. "
        "You ENHANCE a topic or piece of source material into a concise, sequential scene script. "
        "Write narration as plain spoken prose that (1) introduces each scene in one "
        "sentence, (2) lands the key point, (3) reads aloud naturally; you may sharpen wording, "
        "reorder for drama and add concrete, well-known detail, but never invent fabricated "
        "numbers or hard facts. Use a strong visual heading per scene. Keep 'items' as short "
        "punchy on-screen phrases. Prefer the 'title' kind for scene 1 and 'outro' for the last "
        "scene.\n"
        "ESSENCE RULE: preserve the source's concrete specifics — exact names, numbers, mechanisms "
        "and trade-offs — so each scene feels specific, not generic. Distribute the key facts across "
        "the scenes; leave out the least important detail, never the substance." + lang_note
    )
    if prompt and source_text:
        content_block = (
            f"TOPIC / REQUEST:\n{prompt}\n\n"
            f"REFERENCE MATERIAL (enhance and stay faithful to this):\n{source_text[:12000]}\n"
        )
    elif prompt:
        content_block = (
            f"TOPIC / REQUEST:\n{prompt}\n"
            "(No reference material was supplied — build the explainer from your general "
            "knowledge of the topic, keeping commonly-known facts accurate.)\n"
        )
    else:
        content_block = f"SOURCE MATERIAL:\n{source_text[:14000]}\n"
    user = (
        content_block +
        f"\nGOAL: a narrated explainer of ~{target_seconds}s in tone: {tone}. "
        f"Produce about {n_scenes} scenes. Budget the narration to roughly "
        f"{word_budget} spoken words total (~{word_budget // n_scenes} per scene) so the finished "
        f"video matches the target duration. Scene order must follow the topic.\n\n"
        f"{SCENE_SCHEMA}"
    )
    content = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                     model, base_url, max_tokens=16384)
    obj = _extract_json(content)
    if isinstance(obj, dict) and "scenes" in obj:
        scenes = obj["scenes"]
    elif isinstance(obj, list):
        scenes = obj
        obj = {"scenes": obj}
    else:
        raise NimError(f"script response has no scenes: {str(obj)[:200]}")
    if not isinstance(scenes, list) or not scenes:
        raise NimError("LLM returned an empty scene list")

    # Refine the narration until the estimated total matches the requested duration.
    for _ in range(4):
        next_scenes = _adjust_lengths([dict(s) for s in scenes], target_seconds, model, base_url)
        if next_scenes == scenes:
            break
        scenes = next_scenes
    obj["scenes"] = scenes
    return obj


def rewrite_scene(
    scene: dict,
    instruction: str,
    context_lines: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE,
) -> dict:
    system = (
        "You rewrite a single scene of a narrated explainer video. Respect the user's rewrite "
        "instruction. Keep it a *scene*, and PRESERVE the scene's concrete facts (names, numbers, "
        "mechanisms). If the instruction is generic, still produce a clearly richer, more specific "
        "variant. Narration 12-45 words, plain spoken prose."
    )
    user = (
        "CURRENT SCENE (JSON):\n" + json.dumps(scene, ensure_ascii=False) +
        "\n\nNEIGHBORING SCENES FOR CONTEXT:\n" + context_lines +
        "\n\nINSTRUCTION:\n" + instruction + "\n\n" + SCENE_SCHEMA +
        "\nReturn ONLY the single JSON object for this one scene (same shape, no wrapping array)."
    )
    content = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                     model, base_url)
    obj = _extract_json(content)
    if isinstance(obj, dict) and "scenes" in obj and obj["scenes"]:
        obj = obj["scenes"][0]
    return obj


def ping(model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE) -> dict:
    """Cheap call to validate connectivity + model using the server-side key."""
    content = _chat(
        [{"role": "user", "content": "Reply with exactly: ok"}],
        model, base_url, max_tokens=8,
    )
    return {"oks": True, "echo": content[:40]}