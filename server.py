#!/usr/bin/env python3
import json
import os
import re
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{11,17}")


def json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def clean_vin(text):
    cleaned = re.sub(r"[^A-HJ-NPR-Z0-9]", "", (text or "").upper())
    if len(cleaned) >= 17:
        return cleaned[:17]
    match = VIN_RE.search(cleaned)
    return match.group(0) if match else ""


def call_openai_vision(image_base64, media_type):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"vin": "", "error": "OPENAI_API_KEY is not set on the server."}

    model = os.environ.get("OPENAI_VISION_MODEL", "gpt-5.5")
    prompt = (
        "Read this dealership photo and extract the Vehicle Identification Number (VIN). "
        "Return ONLY valid JSON in this exact shape: {\"vin\":\"...\"}. "
        "If no VIN is visible, return {\"vin\":\"\"}. "
        "VIN rules: 17 characters when complete, uppercase, valid characters A-H J-N P-R S-Z and 0-9, never I/O/Q. "
        "Do not guess. Strip spaces, labels, punctuation, and line breaks."
    )
    payload = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{media_type};base64,{image_base64}"},
            ],
        }],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {"vin": "", "error": f"OpenAI vision request failed: HTTP {exc.code}. {detail}"}
    except Exception as exc:
        return {"vin": "", "error": f"Vision request failed: {exc}"}

    output_text = data.get("output_text", "")
    if not output_text:
        parts = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    parts.append(content.get("text", ""))
        output_text = "\n".join(parts)

    try:
        parsed = json.loads(output_text)
        vin = clean_vin(parsed.get("vin", ""))
    except Exception:
        vin = clean_vin(output_text)

    return {"vin": vin, "raw": output_text[:200]}


class KellerScoutHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/vision-vin":
            json_response(self, 404, {"error": "Unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            json_response(self, 400, {"vin": "", "error": "Invalid JSON body"})
            return

        image = payload.get("image", "")
        media_type = payload.get("mediaType", "image/jpeg")
        image = image.split(",", 1)[-1]

        if not image:
            json_response(self, 400, {"vin": "", "error": "Missing image"})
            return

        result = call_openai_vision(image, media_type)
        json_response(self, 200, result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), KellerScoutHandler)
    print(f"Keller Scout running at http://127.0.0.1:{port}/index.html")
    server.serve_forever()
