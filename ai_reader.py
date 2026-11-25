"""Local Pricing extraction. Ollama is optional and runs locally (no paid API)."""
import base64
import io
import json
import math
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import pandas as pd

GROUPS = [
    'OCEAN FREIGHT',
    'ORIGIN LOCAL CHARGES',
    'DESTINATION LOCAL CHARGES',
    'CHI PHÍ NỘI BỘ',
    'CHƯA PHÂN LOẠI'
]

IMAGE_SYSTEM = """You extract freight fees from a pricing-table image.

Return ONLY JSON with these keys:
table_row_count, direction, warnings, fees.

Set direction to "export" or "import" only when explicitly stated; otherwise
use "unknown". Never infer shipment direction from port names.

Count the visible fee rows, excluding headers, and return one fee for every
row in top-to-bottom order. Each fee must contain:
name, rate, unit, currency, note.

Read prices only from the buying-price column, identified by headings such
as "Giá mua", "Giá mua COSCO", "MSC giá mua", "Buying Rate" or "Cost".
Do not use a selling-price or profit column. Pair each price with the fee
on the same row; never shift a price from a neighboring row.

Return numeric prices as numbers without currency symbols or separators.
For example, a cell printed as "$123" must produce rate 123 and currency
USD. Preserve zero as 0. For a text value such as "at cost", set rate to
null and copy that text into note. Never replace text with a nearby number.
Keep the fee name and billing unit as shown; do not use USD as a unit.
Read the billing unit exactly from the unit column; examples include CONT,
HBL, SHPT, SET, pallet and Hour. Never output a generic placeholder such as
"billing unit". Determine currency from the price cell's printed symbol or
code, never from a carrier name in the heading. An unqualified "$" price is
USD; a heading such as "COSCO" is not a currency. If no currency is shown,
use null. Preserve conditions in note when explicitly stated. Do not invent
missing values, omit rows, or add metadata or fee classifications. Use null
and add a warning if a value cannot be read."""

SYSTEM = """You are a freight-forwarding Pricing document extractor
specialized in VIETNAM EXPORT SHIPMENTS.

TASK:
Extract buying costs from the uploaded Pricing document.

STRICT RULES:
1. Extract ONLY information explicitly present in the source.
2. NEVER invent rates, fees, quantities, currencies,
   Incoterms, dates, routes or shipment directions.
3. Use ONLY the buying-cost column, such as:
   "Giá mua KMTC", "Giá mua", "Buying Rate", "Cost".
4. Do NOT extract selling prices as buying costs.
5. Preserve zero-value buying costs.
6. Do NOT calculate profit, markup or selling prices.
7. Do NOT omit a fee merely because its rate is missing.
   Return rate=null and explain in warnings.
8. Preserve the original fee names and billing units.
9. USD, VND, EUR are currencies, NOT billing units.
10. If the document is import or direction is unclear,
    report import or unknown. Never assume export.

FEE CLASSIFICATION:
- OCEAN FREIGHT:
  Sea freight or ocean transportation charges explicitly
  identified as such.
- ORIGIN LOCAL CHARGES:
  Charges explicitly identified as occurring at origin.
- DESTINATION LOCAL CHARGES:
  Charges explicitly identified as occurring at destination.
- CHI PHÍ NỘI BỘ:
  Commission, internal-only costs and internal expenses.

IMPORTANT CLASSIFICATION RULES:
- THC, LSS, Seal, Docs Fee, Handling Fee and Telex Release
  must NOT automatically be classified as Ocean Freight.
- Classify origin or destination ONLY when supported by
  the source document.
- Do NOT infer charge location from Incoterms alone.
- When classification is uncertain, set group to
  CHƯA PHÂN LOẠI, preserve the fee, add a warning,
  and explain the uncertainty in note.
- Never classify THC, LSS, Seal, Docs Fee, Handling Fee
  or Telex Release Fee as OCEAN FREIGHT solely because
  they appear in the same table as ocean freight.
- Do not automatically assume that every surcharge
  belongs to origin or destination local charges.
- Do NOT invent a confident classification.
- Commission must always be classified as internal-only.

QUANTITY RULES:
- If the source explicitly states a total amount,
  quantity may be 1, with an explanatory note.
- If the source does not state quantity, return null.
- Never assume one container or one shipment.

OUTPUT:
Return ONLY a valid JSON object with these keys:
direction, customer, pol, pod, incoterm, valid_until,
commodity, carrier, container_type, container_quantity,
currency, warnings, fees.

Extract customer, commodity, carrier, container type and
container quantity only when explicitly stated in the source.
Return valid_until as YYYY-MM-DD. Use null when missing.

Each fee must contain:
name, rate, unit, quantity, currency,
group, note, conditional.

Allowed group values:
OCEAN FREIGHT
ORIGIN LOCAL CHARGES
DESTINATION LOCAL CHARGES
CHI PHÍ NỘI BỘ
CHƯA PHÂN LOẠI
Use null for missing numerical values.
Use warnings to report missing or uncertain information.

IMPORTANT RULES FOR PRICING TABLES:
1. Extract buying costs ONLY from "Giá mua KMTC" or "Giá mua".
2. NEVER extract buying costs from "Giá bán" or "Profit KMTC".
3. Preserve zero buying costs exactly, including 0 USD.
4. Match every buying cost with its correct fee name and unit.
5. Commission is an internal-only cost, not a selling price.
6. If a buying cost is unclear, use null and add a warning.
7. Do not invent missing information.
IMPORTANT RULES FOR UNITS AND FEE GROUPS:
1. The "unit" field must contain the billing unit:
   CONT, HBL, CBM, W/M, SHIPMENT, TRUCK, etc.
2. NEVER use USD as the billing unit.
   USD is a currency, not a billing unit.
3. Read the billing unit from the column next to the fee name.
4. HCM - INCHEON is an ocean freight charge,
   with unit CONT as shown in the source.
5. THC, SEAL, DOCS FEE, TELEX RELEASE FEE and
   HANDLING FEE are local or documentation charges.
   Classify them as origin or destination only
   when the source provides enough information.
6. If the origin or destination of a charge is unclear,
   add a warning. Do not guess.
7. Commission belongs to CHI PHÍ NỘI BỘ.
8. Preserve zero buying costs exactly."""

def _text_from_excel(data, ext):
    if ext == '.csv':
        for enc in ('utf-8-sig','cp1258','latin1'):
            try:
                df = pd.read_csv(io.BytesIO(data), header=None, encoding=enc, dtype=str)
                return df.fillna('').to_csv(index=False, header=False), []
            except UnicodeError:
                continue
        raise ValueError('Không đọc được mã hóa CSV.')
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
    parts=[]
    for name, df in sheets.items():
        parts.append('SHEET: '+str(name)+'\n'+df.fillna('').to_csv(index=False,header=False))
    return '\n'.join(parts), []

def read_source(filename, data):
    ext=Path(filename).suffix.lower()
    if ext in ('.xlsx','.xls','.csv'):
        text, warnings = _text_from_excel(data,ext)
        return text, [], warnings
    if ext=='.txt':
        return data.decode('utf-8-sig',errors='replace'), [], []
    if ext=='.pdf':
        try:
            import fitz
        except ImportError:
            raise RuntimeError('Cần cài pymupdf để đọc PDF: pip install pymupdf')
        pdf=fitz.open(stream=data,filetype='pdf')
        text='\n'.join(page.get_text(sort=True) for page in pdf)
        if len(text.strip())>50:
            return text, [], []
        # Scanned PDF: use up to 3 pages, local vision model required.
        imgs=[]
        for page in list(pdf)[:3]:
            imgs.append(page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).tobytes('png'))
        return '',imgs,['PDF scan: sử dụng tối đa 3 trang đầu với mô hình AI thị giác.']
    if ext in ('.png','.jpg','.jpeg','.webp'):
        return '',[data],[]
    raise ValueError('Định dạng chưa hỗ trợ. Hãy dùng XLSX, XLS, CSV, PDF, TXT, PNG, JPG hoặc WEBP.')


def _table_row_bands(grayscale):
    pixels = np.asarray(grayscale)
    if pixels.ndim != 2 or not pixels.size:
        return []

    active_rows = np.flatnonzero(
        np.count_nonzero(pixels < 100, axis=1) >= pixels.shape[1] * 0.55
    )
    if not active_rows.size:
        return []

    groups = []
    for row in active_rows:
        if not groups or row > groups[-1][-1] + 1:
            groups.append([int(row)])
        else:
            groups[-1].append(int(row))
    return groups


def _make_fee_row_images(image):
    from PIL import Image, ImageOps

    bands = _table_row_bands(ImageOps.grayscale(image))
    if len(bands) < 2:
        return []

    header = image.crop((0, 0, image.width, bands[0][0]))
    row_images = []
    for upper, lower in zip(bands, bands[1:]):
        row = image.crop(
            (
                0,
                max(0, upper[-1] - 2),
                image.width,
                min(image.height, lower[0] + 3),
            )
        )
        if header.height:
            composite = Image.new(
                "RGB",
                (image.width, header.height + 8 + row.height),
                "white",
            )
            composite.paste(header, (0, 0))
            composite.paste(row, (0, header.height + 8))
        else:
            composite = row

        scale = min(3.0, 1500 / max(composite.size))
        if scale != 1:
            composite = composite.resize(
                (
                    max(1, round(composite.width * scale)),
                    max(1, round(composite.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        row_images.append(composite)
    return row_images


def _estimate_table_rows(grayscale):
    bands = _table_row_bands(grayscale)
    return len(bands) - 1 if len(bands) >= 2 else None


def _prepare_vision_image(data):
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Cần cài Pillow để xử lý ảnh bảng giá: pip install Pillow"
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ValueError("Không đọc được dữ liệu ảnh bảng giá.") from exc

    grayscale = ImageOps.grayscale(image)
    bounds = grayscale.point(lambda pixel: 255 if pixel < 245 else 0).getbbox()
    estimated_rows = None
    fee_row_images = []
    if bounds:
        left, top, right, bottom = bounds
        padding_x = max(4, int((right - left) * 0.02))
        padding_y = max(4, int((bottom - top) * 0.02))
        bounds = (
            max(0, left - padding_x),
            max(0, top - padding_y),
            min(image.width, right + padding_x),
            min(image.height, bottom + padding_y),
        )
        image = image.crop(bounds)
        cropped_grayscale = grayscale.crop(bounds)
        estimated_rows = _estimate_table_rows(cropped_grayscale)
        if estimated_rows:
            fee_row_images = _make_fee_row_images(image)

    scale = min(3.0, 1800 / max(image.size))
    if scale != 1:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    image = ImageEnhance.Contrast(image).enhance(1.15)
    image = ImageEnhance.Sharpness(image).enhance(1.2)
    output = io.BytesIO()
    image.save(output, format="PNG")
    row_bytes = []
    for row_image in fee_row_images:
        row_output = io.BytesIO()
        row_image.save(row_output, format="PNG")
        row_bytes.append(row_output.getvalue())
    return output.getvalue(), estimated_rows, row_bytes


def _verify_fee_row_price(image_data, model, base_url, timeout):
    prompt = (
        "The image contains the buying-price column header followed by exactly "
        "one fee row from the same pricing table. Read only the price cell in "
        "that row, under the buying-price column. Return JSON with one key "
        '"price_text": the exact cell text, including any currency symbol. '
        "Examples: \"$35\", \"45,000\", \"at cost\". Do not copy a symbol or "
        "note from another row. The table header identifies the correct price "
        "column; ignore red annotations that are outside the fee row."
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image_data).decode("ascii")],
            }
        ],
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.URLError as exc:
        raise RuntimeError("Không xác minh được giá của dòng trên ảnh.") from exc

    raw = result.get("message", {}).get("content", "").strip()
    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AI không trả được ô giá dạng JSON hợp lệ.") from exc
    price_text = extracted.get("price_text") if isinstance(extracted, dict) else None
    if not isinstance(price_text, str) or not price_text.strip():
        raise ValueError("AI không đọc được nội dung ô giá của dòng.")
    return price_text.strip()


def _parse_verified_price(price_text):
    normalized = price_text.upper().replace("US$", "USD")
    if re.search(r"\bAT\s+COST\b", normalized, re.I):
        return None, None, price_text

    currency_match = re.search(r"\b(USD|VND|EUR|CNY|AUD|CAD|SGD|JPY)\b", normalized)
    if "$" in normalized or currency_match and currency_match.group(1) == "USD":
        currency = "USD"
    elif currency_match:
        currency = currency_match.group(1)
    else:
        currency = None

    numeric_text = re.sub(
        r"USD|VND|EUR|CNY|AUD|CAD|SGD|JPY|[$€£¥,\s]",
        "",
        normalized,
    )
    if not re.fullmatch(r"\d+(?:\.\d+)?", numeric_text):
        return None, currency, price_text

    rate = float(numeric_text)
    if not math.isfinite(rate):
        return None, currency, price_text
    return rate, currency, price_text


def ask_ollama(text, images, model='qwen2.5vl:3b', base_url='http://127.0.0.1:11434', timeout=180):
    if not text.strip() and not images:
        raise ValueError('Tệp không chứa nội dung có thể đọc.')
    is_image_extraction = bool(images)
    expected_image_rows = None
    fee_row_images = []
    if is_image_extraction:
        system = IMAGE_SYSTEM
        content = (
            "Read every visible fee row from the attached pricing table. "
            "Match each fee to the buying-price cell on the same row. "
            "Include zero prices and text values such as at cost. "
            + (f"\nAdditional source text:\n{text[:50000]}" if text.strip() else "")
        )
    else:
        system = SYSTEM
        content='Extract the following Pricing source:\n'+text[:50000]
    msg={'role':'user','content':content}
    if images:
        prepared_images = []
        detected_counts = []
        for image in images:
            prepared, count, row_images = _prepare_vision_image(image)
            prepared_images.append(base64.b64encode(prepared).decode('ascii'))
            fee_row_images.extend(row_images)
            if count:
                detected_counts.append(count)
        msg['images'] = prepared_images
        if detected_counts:
            expected_image_rows = sum(detected_counts)
            msg["content"] += (
                f"\nHorizontal table borders indicate exactly "
                f"{expected_image_rows} fee rows. Return one item for every "
                "row, in order."
            )
    payload={'model':model,'stream':False,'format':'json','messages':[{'role':'system','content':system},msg], 'options':{'temperature':0}}
    req=urllib.request.Request(base_url.rstrip('/')+'/api/chat',data=json.dumps(payload).encode('utf-8'),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as res:
            response=json.load(res)
    except urllib.error.URLError as exc:
        raise RuntimeError('Không kết nối được Ollama. Hãy mở Ollama và tải mô hình trước.') from exc
    raw=response.get('message',{}).get('content','').strip()
    try:
        result=json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.S)

        if not match: raise ValueError('AI không trả về JSON hợp lệ. Vui lòng thử lại.')
        result=json.loads(match.group())
    if not isinstance(result,dict) or not isinstance(result.get('fees'),list):
        raise ValueError('AI trả về cấu trúc không hợp lệ.')
    if is_image_extraction:
        try:
            counted_rows = int(result.get("table_row_count"))
        except (TypeError, ValueError):
            counted_rows = None
        if expected_image_rows is not None and len(result["fees"]) != expected_image_rows:
            warnings = result.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(
                f"Ảnh có {expected_image_rows} dòng theo đường kẻ bảng nhưng "
                f"AI trích xuất {len(result['fees'])}; cần kiểm tra để tránh "
                "bỏ sót phí."
            )
            result["warnings"] = warnings
        elif expected_image_rows is None and counted_rows is None:
            warnings = result.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(
                "AI không trả số dòng phí trong ảnh; cần đối chiếu thủ công "
                "để bảo đảm không bỏ sót."
            )
            result["warnings"] = warnings
        elif expected_image_rows is None and counted_rows != len(result["fees"]):
            warnings = result.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(
                f"AI đếm được {counted_rows} dòng phí nhưng chỉ trích xuất "
                f"{len(result['fees'])}; cần kiểm tra lại ảnh trước khi báo giá."
            )
            result["warnings"] = warnings
        elif (
            expected_image_rows is not None
            and len(result["fees"]) == expected_image_rows
            and len(fee_row_images) == expected_image_rows
        ):
            for index, fee in enumerate(result["fees"]):
                if not isinstance(fee, dict):
                    continue
                currency = str(fee.get("currency") or "").upper().strip()
                note = str(fee.get("note") or "")
                try:
                    rate = float(fee["rate"]) if fee.get("rate") is not None else None
                except (TypeError, ValueError):
                    rate = None
                has_rate = rate is not None and math.isfinite(rate)
                note_says_at_cost = bool(re.search(r"\bat\s+cost\b", note, re.I))
                needs_verification = (
                    currency not in ("USD", "US$")
                    or note_says_at_cost and has_rate
                    or not has_rate and not note_says_at_cost
                )
                if not needs_verification:
                    continue

                try:
                    price_text = _verify_fee_row_price(
                        fee_row_images[index],
                        model,
                        base_url,
                        timeout,
                    )
                except (RuntimeError, ValueError) as exc:
                    warnings = result.get("warnings")
                    if not isinstance(warnings, list):
                        warnings = []
                    warnings.append(
                        f"Dòng {index + 1}: {exc} Giữ tiền tệ chưa xác định "
                        "để Sales kiểm tra."
                    )
                    result["warnings"] = warnings
                    continue

                rate, verified_currency, verified_text = _parse_verified_price(
                    price_text
                )
                fee["rate"] = rate
                fee["currency"] = verified_currency
                fee["currency_mark"] = (
                    "$" if "$" in verified_text else verified_currency or ""
                )
                fee["note"] = (
                    verified_text
                    if re.search(r"\bat\s+cost\b", verified_text, re.I)
                    else ""
                    if note_says_at_cost
                    else note
                )
    return result


def _openai_chat_content(messages, model, base_url, api_key, timeout):
    endpoint = urlsplit(base_url)
    if (
        not endpoint.netloc
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.scheme != "https"
        and not (
            endpoint.scheme == "http"
            and endpoint.hostname in ("localhost", "127.0.0.1", "::1")
        )
    ):
        raise ValueError(
            "AI Cloud endpoint must use HTTPS and must not contain credentials."
        )

    payload = {
        "model": model,
        "stream": False,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "temperature": 0,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            message = "AI Cloud quota or rate limit reached (HTTP 429)."
        elif error.code in (401, 403):
            message = "AI Cloud rejected the request; check API key permissions."
        else:
            message = f"AI Cloud request failed (HTTP {error.code})."
        error.close()
        raise RuntimeError(message) from None
    except urllib.error.URLError:
        raise RuntimeError(
            "AI Cloud is unavailable; check the endpoint and network connection."
        ) from None

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("AI Cloud returned an unsupported response format.") from None
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI Cloud returned empty content.")
    return content.strip()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _parse_ai_json(raw):
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError("AI Cloud did not return valid JSON.") from None
        try:
            result = json.loads(match.group())
        except json.JSONDecodeError:
            raise ValueError("AI Cloud did not return valid JSON.") from None
    if not isinstance(result, dict) or not isinstance(result.get("fees"), list):
        raise ValueError("AI Cloud returned an unsupported data structure.")
    return result


def _verify_openai_fee_row_price(image_data, model, base_url, api_key, timeout):
    prompt = (
        "The image contains the buying-price column header followed by exactly "
        "one fee row from the same pricing table. Read only the price cell in "
        "that row, under the buying-price column. Return JSON with one key "
        '"price_text": the exact cell text, including any currency symbol. '
        "Examples: \"$35\", \"45,000\", \"at cost\". Do not copy a symbol or "
        "note from another row."
    )
    image = base64.b64encode(image_data).decode("ascii")
    content = _openai_chat_content(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image}"
                        },
                    },
                ],
            }
        ],
        model,
        base_url,
        api_key,
        timeout,
    )
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("AI Cloud returned invalid price verification JSON.") from None
    price_text = result.get("price_text") if isinstance(result, dict) else None
    if not isinstance(price_text, str) or not price_text.strip():
        raise ValueError("AI Cloud could not read the fee price cell.")
    return price_text.strip()


def ask_openai_compatible(
    text,
    images,
    model,
    base_url,
    api_key,
    timeout=180,
):
    if not text.strip() and not images:
        raise ValueError("Tệp không chứa nội dung có thể đọc.")
    if not model or not base_url or not api_key:
        raise ValueError(
            "Configure ai.model, ai.base_url, and ai.api_key in Streamlit Secrets."
        )

    is_image_extraction = bool(images)
    expected_image_rows = None
    fee_row_images = []
    user_content = []
    if images:
        prompt = (
            "Read every visible fee row from the attached pricing table. "
            "Match each fee to the buying-price cell on the same row. "
            "Include zero prices and text values such as at cost. "
            + (f"\nAdditional source text:\n{text[:50000]}" if text.strip() else "")
        )
        user_content.append({"type": "text", "text": prompt})
        detected_counts = []
        for image in images:
            prepared, count, row_images = _prepare_vision_image(image)
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(prepared).decode("ascii")
                    },
                }
            )
            fee_row_images.extend(row_images)
            if count:
                detected_counts.append(count)
        if detected_counts:
            expected_image_rows = sum(detected_counts)
            user_content[0]["text"] += (
                f"\nHorizontal table borders indicate exactly "
                f"{expected_image_rows} fee rows. Return one item for every "
                "row, in order."
            )
        user_message = {"role": "user", "content": user_content}
        system = IMAGE_SYSTEM
    else:
        system = SYSTEM
        user_message = {
            "role": "user",
            "content": "Extract the following Pricing source:\n" + text[:50000],
        }

    raw = _openai_chat_content(
        [
            {"role": "system", "content": system},
            user_message,
        ],
        model,
        base_url,
        api_key,
        timeout,
    )
    result = _parse_ai_json(raw)
    if is_image_extraction:
        try:
            counted_rows = int(result.get("table_row_count"))
        except (TypeError, ValueError):
            counted_rows = None
        warnings = result.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        if expected_image_rows is not None and len(result["fees"]) != expected_image_rows:
            warnings.append(
                f"Ảnh có {expected_image_rows} dòng theo đường kẻ bảng nhưng "
                f"AI trích xuất {len(result['fees'])}; cần kiểm tra để tránh "
                "bỏ sót phí."
            )
        elif expected_image_rows is None and counted_rows is None:
            warnings.append(
                "AI không trả số dòng phí trong ảnh; cần đối chiếu thủ công "
                "để bảo đảm không bỏ sót."
            )
        elif expected_image_rows is None and counted_rows != len(result["fees"]):
            warnings.append(
                f"AI đếm được {counted_rows} dòng phí nhưng chỉ trích xuất "
                f"{len(result['fees'])}; cần kiểm tra lại ảnh trước khi báo giá."
            )
        result["warnings"] = warnings

        if (
            expected_image_rows is not None
            and len(result["fees"]) == expected_image_rows
            and len(fee_row_images) == expected_image_rows
        ):
            for index, fee in enumerate(result["fees"]):
                if not isinstance(fee, dict):
                    continue
                currency = str(fee.get("currency") or "").upper().strip()
                note = str(fee.get("note") or "")
                try:
                    rate = (
                        float(fee["rate"])
                        if fee.get("rate") is not None
                        else None
                    )
                except (TypeError, ValueError):
                    rate = None
                has_rate = rate is not None and math.isfinite(rate)
                note_says_at_cost = bool(
                    re.search(r"\bat\s+cost\b", note, re.I)
                )
                needs_verification = (
                    currency not in ("USD", "US$")
                    or note_says_at_cost and has_rate
                    or not has_rate and not note_says_at_cost
                )
                if not needs_verification:
                    continue

                try:
                    price_text = _verify_openai_fee_row_price(
                        fee_row_images[index],
                        model,
                        base_url,
                        api_key,
                        timeout,
                    )
                except (RuntimeError, ValueError) as error:
                    result["warnings"].append(
                        f"Dòng {index + 1}: {error} Giữ tiền tệ chưa xác định "
                        "để Sales kiểm tra."
                    )
                    continue

                rate, verified_currency, verified_text = _parse_verified_price(
                    price_text
                )
                fee["rate"] = rate
                fee["currency"] = verified_currency
                fee["currency_mark"] = (
                    "$" if "$" in verified_text else verified_currency or ""
                )
                fee["note"] = (
                    verified_text
                    if re.search(r"\bat\s+cost\b", verified_text, re.I)
                    else ""
                    if note_says_at_cost
                    else note
                )
    return result


def normalize_quote_metadata(result):
    fields = {
        "quote_customer": str(result.get("customer") or "").strip(),
        "quote_pol": str(result.get("pol") or "").strip(),
        "quote_pod": str(result.get("pod") or "").strip(),
        "quote_incoterm": "",
        "quote_container": "",
        "quote_qty": None,
        "quote_commodity": str(result.get("commodity") or "").strip(),
        "quote_carrier": str(result.get("carrier") or "").strip(),
        "quote_valid_until": None,
    }
    warnings = []

    raw_incoterm = str(result.get("incoterm") or "").upper()
    incoterm_match = re.search(
        r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b",
        raw_incoterm,
    )
    if incoterm_match:
        fields["quote_incoterm"] = incoterm_match.group(1)
    elif raw_incoterm.strip():
        warnings.append(
            "Incoterm trong file chưa được nhận diện; Sales cần kiểm tra."
        )

    raw_container = re.sub(
        r"[^A-Z0-9]", "", str(result.get("container_type") or "").upper()
    )
    if raw_container in ("20GP", "40GP", "40HC", "20RF", "40RF"):
        fields["quote_container"] = raw_container
    elif raw_container:
        warnings.append(
            "Loại container trong file chưa được nhận diện; Sales cần kiểm tra."
        )

    try:
        quantity = float(result.get("container_quantity"))
    except (TypeError, ValueError):
        quantity = None
    if (
        quantity is not None
        and math.isfinite(quantity)
        and quantity > 0
        and quantity.is_integer()
    ):
        fields["quote_qty"] = int(quantity)
    elif result.get("container_quantity") not in (None, ""):
        warnings.append(
            "Số container trong file không hợp lệ; Sales cần kiểm tra."
        )

    raw_valid_until = str(result.get("valid_until") or "").strip()
    if raw_valid_until:
        try:
            fields["quote_valid_until"] = date.fromisoformat(raw_valid_until)
        except ValueError:
            warnings.append(
                "Ngày hết hiệu lực trong file không rõ; Sales cần kiểm tra."
            )

    required_fields = (
        ("quote_customer", "khách hàng"),
        ("quote_pol", "cảng xuất (POL)"),
        ("quote_pod", "cảng đến (POD)"),
        ("quote_incoterm", "Incoterm"),
        ("quote_container", "loại container"),
        ("quote_qty", "số container"),
        ("quote_valid_until", "ngày hết hiệu lực"),
    )
    for key, label in required_fields:
        if fields[key] in ("", None):
            warnings.append(
                f"Không tìm thấy {label} trong file; báo giá nháp để trống "
                "trường này."
            )

    return fields, warnings


def normalize(result):
    rows = []
    raw_warnings = result.get("warnings", [])
    if isinstance(raw_warnings, list):
        warnings = [str(item) for item in raw_warnings if str(item).strip()]
    elif raw_warnings:
        warnings = [str(raw_warnings)]
    else:
        warnings = []

    direction = str(result.get("direction") or "unknown").lower()
    direction_tokens = set(re.findall(r"[a-z]+", direction))
    indicates_export = (
        "export" in direction_tokens
        and "import" not in direction_tokens
        and not direction_tokens.intersection({"not", "unknown", "unclear"})
    )
    if not indicates_export:
        warnings.append(
            f"AI nhận diện hướng vận chuyển là {direction}. "
            "KHÔNG tự coi đây là báo giá xuất; Sales phải kiểm tra POL/POD."
        )

    for idx, fee in enumerate(result.get("fees", []), 1):
        if not isinstance(fee, dict):
            warnings.append(f"Dòng {idx}: dữ liệu phí không hợp lệ, cần kiểm tra.")
            continue

        name = str(fee.get("name") or "").strip()
        if not name:
            warnings.append(f"Dòng {idx}: thiếu tên phí, không thể rà soát.")
            continue

        try:
            rate = float(fee["rate"]) if fee.get("rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        note = str(fee.get("note") or "")
        if re.search(r"\bat\s+cost\b", note, re.I):
            rate = None
        if rate is not None and (not math.isfinite(rate) or rate < 0):
            rate = None
        if rate is None:
            warnings.append(
                f"{name}: thiếu/không hợp lệ giá mua; Sales phải nhập giá USD "
                "trước khi hoàn thiện báo giá."
            )

        if "currency_mark" in fee:
            currency_mark = str(fee.get("currency_mark") or "").upper().strip()
            if "$" in currency_mark or currency_mark in ("USD", "US$"):
                currency = "USD"
            elif currency_mark in ("VND", "EUR", "CNY", "AUD", "CAD", "SGD", "JPY"):
                currency = currency_mark
            else:
                currency = ""
        else:
            currency = str(
                fee.get("currency") or result.get("currency") or ""
            ).upper().strip()
        if currency not in ("USD", "US$"):
            warnings.append(
                f"{name}: đơn vị tiền {currency or 'không rõ'}; "
                "chưa quy đổi sang USD, Sales phải kiểm tra giá mua."
            )
            if rate is not None:
                source_rate = format(rate, "g")
                note = "; ".join(
                    part
                    for part in (
                        note,
                        f"Giá nguồn: {source_rate}; cần xác nhận tiền tệ",
                    )
                    if part
                )
            rate = None

        try:
            quantity = (
                float(fee["quantity"])
                if fee.get("quantity") is not None
                else None
            )
        except (TypeError, ValueError):
            quantity = None
        if (
            quantity is None
            or not math.isfinite(quantity)
            or quantity <= 0
        ):
            warnings.append(
                f"{name}: chưa xác định số lượng tính phí; Sales phải nhập "
                "số lượng trước khi hoàn thiện báo giá."
            )
            quantity = None

        group = str(fee.get("group") or "").upper().strip()
        is_internal = bool(
            re.search(r"commission|internal.only|internal expense", name, re.I)
        )
        requires_review = bool(
            re.search(
                r"\bTHC\b|\bLSS\b|\bSEAL\b|"
                r"TELEX RELEASE|DOCS? FEE|DOCUMENTATION|HANDLING FEE",
                name,
                re.I,
            )
        )

        if is_internal:
            group = "CHI PHÍ NỘI BỘ"
        elif group not in GROUPS:
            group = "CHƯA PHÂN LOẠI"
            warnings.append(f"{name}: chưa xác định được nhóm phí.")
        elif group == "OCEAN FREIGHT" and requires_review:
            group = "CHƯA PHÂN LOẠI"
            warnings.append(
                f"{name}: AI phân loại vào Ocean Freight, Sales cần kiểm tra lại."
            )

        if group == "CHƯA PHÂN LOẠI":
            warnings.append(
                f"{name}: chưa được phân loại, không tự động đưa vào báo giá."
            )

        unit = str(fee.get("unit") or "").strip()
        if not unit or unit.upper() in ("USD", "US$"):
            warnings.append(
                f"{name}: chưa xác định được đơn vị tính phí; Sales phải kiểm tra."
            )
            unit = "CHƯA RÕ"

        if fee.get("conditional"):
            warnings.append(
                f"{name}: khoản phí có điều kiện, phải xác nhận trước khi báo khách."
            )
            note = ("CONDITIONAL - " + note).strip()

        rows.append(
            {
                "Nhóm phí": group,
                "Khoản phí": name,
                "Đơn vị": unit,
                "Số lượng": quantity,
                "Giá mua (USD)": rate,
                "Giá bán (USD)": rate,
                "Ghi chú": note,
                "Hiển thị PDF": False,
            }
        )

    columns = [
        "Nhóm phí",
        "Khoản phí",
        "Đơn vị",
        "Số lượng",
        "Giá mua (USD)",
        "Giá bán (USD)",
        "Ghi chú",
        "Hiển thị PDF",
    ]
    return pd.DataFrame(rows, columns=columns), warnings


def validate_candidate(rows):
    errors = []
    if rows.empty:
        return ["Không có dòng phí để kiểm tra."]

    if rows["Khoản phí"].fillna("").astype(str).str.strip().eq("").any():
        errors.append("Mọi dòng phí phải có tên trước khi thêm.")

    rates = pd.to_numeric(rows["Giá mua (USD)"], errors="coerce")
    invalid_rates = rates.isna() | rates.lt(0) | rates.isin([math.inf, -math.inf])
    if invalid_rates.any():
        errors.append(
            "Cần nhập giá mua USD hợp lệ cho mọi dòng; không tự quy đổi tiền tệ."
        )

    quantities = pd.to_numeric(rows["Số lượng"], errors="coerce")
    invalid_quantities = (
        quantities.isna()
        | quantities.le(0)
        | quantities.isin([math.inf, -math.inf])
    )
    if invalid_quantities.any():
        errors.append("Cần nhập số lượng lớn hơn 0 cho mọi dòng.")

    units = rows["Đơn vị"].fillna("").astype(str).str.strip().str.upper()
    if units.isin(("", "USD", "US$", "CHƯA RÕ")).any():
        errors.append("Cần xác nhận đơn vị tính phí cho mọi dòng.")

    return errors
