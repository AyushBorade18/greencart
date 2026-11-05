"""
supa.py - Supabase: who is allowed in (Auth) and where pictures live (Storage).

How it fits with MySQL
  * Supabase Auth checks the email + password and the confirmation email.
    It tells us the person's Supabase id and email. NOTHING else about the
    person is stored there except the sign-up form data (user metadata).
  * The first time a confirmed person logs in, accounts.py creates their
    row in MySQL (customer / seller) and stores the Supabase id in
    auth_id. From then on MySQL is the single source of truth.
  * Pictures are uploaded to a public Supabase Storage bucket; MySQL keeps
    only the picture's web address.
"""
import uuid

import requests

from . import config


class AuthError(Exception):
    def __init__(self, message, code="", needs_confirmation=False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.needs_confirmation = needs_confirmation


def _headers():
    return {"apikey": config.SUPABASE_PUBLISHABLE_KEY, "Content-Type": "application/json"}


def _read_error(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    text = data.get("msg") or data.get("error_description") or data.get("message") or "Authentication failed."
    code = data.get("error_code") or data.get("error") or ""
    return text, str(code)


def _post(path, **kwargs):
    if not config.SUPABASE_READY:
        raise AuthError("Sign-in is not set up yet: SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are missing.")
    try:
        return requests.post(config.SUPABASE_URL + path, headers=_headers(), timeout=15, **kwargs)
    except requests.RequestException:
        raise AuthError("Could not reach the sign-in service. Please try again in a moment.")


def _confirmed(user):
    return bool(user.get("email_confirmed_at") or user.get("confirmed_at"))


# ---------------------------------------------------------------------
#  Sign up / sign in / resend
# ---------------------------------------------------------------------
def sign_up(email, password, metadata, redirect_to):
    """Create the Supabase user. Returns {"already_registered": bool, "user": {...}}."""
    r = _post("/auth/v1/signup", params={"redirect_to": redirect_to},
              json={"email": email, "password": password, "data": metadata})
    if r.status_code >= 400:
        text, code = _read_error(r)
        low = text.lower()
        if r.status_code == 429 or "rate_limit" in code:
            raise AuthError("Too many emails were sent just now. Please wait a few minutes and try again.", code)
        if "already" in low or code == "user_already_exists":
            return {"already_registered": True, "user": {}}
        if "email_address_invalid" in code or ("invalid" in low and "email" in low):
            raise AuthError("Please enter a valid email address.", code)
        if "weak_password" in code or ("password" in low and "least" in low):
            raise AuthError("That password is too weak. Use at least 6 characters.", code)
        raise AuthError(text, code)

    data = r.json()
    user = data.get("user", data)
    identities = user.get("identities")
    already = identities is not None and len(identities) == 0     # Supabase hides duplicates this way
    if config.REQUIRE_EMAIL_CONFIRMATION and _confirmed(user) and not already:
        # A brand-new user must NOT be confirmed yet. If they are, "Confirm email"
        # is switched off in Supabase and nobody's email is being verified.
        raise AuthError("Email confirmation is switched off in Supabase. Switch on 'Confirm email' "
                        "(Authentication > Sign In / Providers > Email), or set "
                        "REQUIRE_EMAIL_CONFIRMATION=false while testing.")
    return {"already_registered": already, "user": user}


def sign_in(email, password):
    """Check the password with Supabase. Returns {"id", "email", "metadata"}."""
    r = _post("/auth/v1/token", params={"grant_type": "password"},
              json={"email": email, "password": password})
    if r.status_code >= 400:
        text, code = _read_error(r)
        low = text.lower()
        if code == "email_not_confirmed" or "not confirmed" in low:
            raise AuthError("Please confirm your email first. Check your inbox for the confirmation link.",
                            code, needs_confirmation=True)
        if r.status_code == 429:
            raise AuthError("Too many attempts. Please wait a few minutes and try again.", code)
        if code in ("invalid_credentials", "invalid_grant") or "invalid login" in low:
            raise AuthError("Wrong email or password.", code)
        raise AuthError(text, code)
    user = r.json().get("user") or {}
    if config.REQUIRE_EMAIL_CONFIRMATION and not _confirmed(user):
        raise AuthError("Please confirm your email first. Check your inbox for the confirmation link.",
                        needs_confirmation=True)
    return {"id": user.get("id") or "",
            "email": (user.get("email") or email).strip().lower(),
            "metadata": user.get("user_metadata") or {}}


def resend_confirmation(email, redirect_to):
    r = _post("/auth/v1/resend", params={"redirect_to": redirect_to},
              json={"type": "signup", "email": email})
    if r.status_code == 429:
        raise AuthError("Please wait a minute before asking for another email.")
    if r.status_code >= 400:
        text, code = _read_error(r)
        raise AuthError(text, code)


# ---------------------------------------------------------------------
#  Picture upload  (Supabase Storage)
# ---------------------------------------------------------------------
ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_BYTES = 2 * 1024 * 1024


def _looks_like_image(data):
    return (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _storage_headers(content_type):
    key = config.SUPABASE_SECRET_KEY
    headers = {"apikey": key, "Content-Type": content_type}
    if key.startswith("eyJ"):                 # old-style JWT keys also need this header
        headers["Authorization"] = "Bearer " + key
    return headers


def _create_bucket():
    """Create the public bucket if it does not exist yet. True when it worked."""
    try:
        r = requests.post(config.SUPABASE_URL + "/storage/v1/bucket",
                          headers=_storage_headers("application/json"),
                          json={"id": config.SUPABASE_BUCKET, "name": config.SUPABASE_BUCKET, "public": True},
                          timeout=15)
    except requests.RequestException:
        return False
    return r.status_code < 400 or "already exists" in r.text.lower()


MAX_SIDE = 1200          # longest side in pixels (product pages show at most this much)


def _optimise(data, mime):
    """
    Shrink a picture before it is stored: at most 1200 px on the long side, JPEG quality 82.
    A 2 MB phone photo becomes roughly 100-200 KB, so pages load far faster on a phone.
    If anything goes wrong the original file is used unchanged.
    Returns (bytes, mime type, file extension).
    """
    try:
        import io
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if img.mode in ("RGBA", "LA", "P"):               # flatten transparency on white
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        smaller = out.getvalue()
        if len(smaller) < len(data):
            return smaller, "image/jpeg", "jpg"
    except Exception:
        pass
    return data, mime, ALLOWED[mime]


def save_image(file_storage, folder):
    """
    Upload a picture and return its public web address, or None when no file
    was chosen. Raises ValueError with a clear message if it cannot be saved.
    """
    if not file_storage or not file_storage.filename:
        return None
    mime = file_storage.mimetype
    if mime not in ALLOWED:
        raise ValueError("Please upload a PNG, JPG or WEBP picture.")
    data = file_storage.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("The picture is too large (maximum 2 MB).")
    if not _looks_like_image(data):
        raise ValueError("That file is not a valid picture.")

    if not (config.SUPABASE_URL and config.SUPABASE_SECRET_KEY):
        raise ValueError("Picture upload is not set up: add SUPABASE_URL and SUPABASE_SECRET_KEY "
                         "to the website settings.")

    data, mime, extension = _optimise(data, mime)
    name = "%s/%s.%s" % (folder, uuid.uuid4().hex, extension)
    url = "%s/storage/v1/object/%s/%s" % (config.SUPABASE_URL, config.SUPABASE_BUCKET, name)

    def upload():
        return requests.post(url, headers=_storage_headers(mime), data=data, timeout=30)

    try:
        r = upload()
        if r.status_code in (400, 404) and "bucket" in r.text.lower() and _create_bucket():
            r = upload()                       # first upload ever: the bucket was just created
    except requests.RequestException:
        raise ValueError("Picture upload failed. Please try again.")
    if r.status_code in (401, 403):
        raise ValueError("Picture upload was refused: check SUPABASE_SECRET_KEY.")
    if r.status_code >= 400:
        raise ValueError("Picture upload failed. Check the Supabase Storage bucket '%s'." % config.SUPABASE_BUCKET)
    return "%s/storage/v1/object/public/%s/%s" % (config.SUPABASE_URL, config.SUPABASE_BUCKET, name)
