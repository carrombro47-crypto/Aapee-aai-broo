"""
MyProjects MS - Video Download/Stream API for Vercel
Handles /pw route for streaming video content with CORS & token support
"""

import os
import logging
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, parse_qsl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from datetime import datetime

# Global session for cookie persistence across requests
_session = None

def get_session():
    """Get or create a requests session with retry strategy."""
    global _session
    if _session is None:
        _session = requests.Session()
        # Retry strategy for transient failures
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    return _session

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "HEAD", "OPTIONS"], "allow_headers": ["*"]}})

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
UPSTREAM_TIMEOUT = 30
UPSTREAM_MAX_RETRIES = 3
CHUNK_SIZE = 8192

UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

# ═══════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def extract_token_from_url(url: str) -> str:
    """Extract token parameter from URL if present."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'token' in params:
            return params['token'][0]
    except Exception:
        pass
    return ""


def fetch_upstream_with_retry(url: str, token: str = ""):
    """
    Advanced fetch with CloudFront signed URL support.
    - Preserves URL parameter order (critical for signatures)
    - Smart retry: No retry on 403/401 auth errors
    - Extracts & logs Key-Pair-ID if present
    - HEAD request first for auth without bandwidth waste
    - Proper referer/origin for signed URLs
    """
    session = get_session()
    
    # ────────────────────────────────────────────────────────────────
    # Parse URL to extract CloudFront auth params
    # ────────────────────────────────────────────────────────────────
    parsed_url = urlparse(url)
    query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    
    # Log important CloudFront params
    cf_params = {}
    for key in ['Key-Pair-Id', 'Signature', 'Policy', 'parentId', 'childId', 'videoId']:
        if key in query_params:
            cf_params[key] = query_params[key][:50] + ('...' if len(query_params[key]) > 50 else '')
    
    if cf_params:
        logger.info(f"CloudFront params detected: {cf_params}")
    
    # ────────────────────────────────────────────────────────────────
    # Build headers with advanced auth support
    # ────────────────────────────────────────────────────────────────
    headers = dict(UPSTREAM_HEADERS)
    
    # Add token to multiple header formats for compatibility
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Auth-Token"] = token
        headers["X-Access-Token"] = token
        logger.info(f"Token auth enabled (length: {len(token)})")
    
    # Forward client cookies
    client_cookies = {}
    if request.headers.get("Cookie"):
        for cookie_part in request.headers.get("Cookie", "").split(";"):
            if "=" in cookie_part:
                k, v = cookie_part.strip().split("=", 1)
                client_cookies[k.strip()] = v.strip()
        headers["Cookie"] = request.headers["Cookie"]
        logger.info(f"Client cookies forwarded: {len(client_cookies)} cookies")
    
    # CloudFront viewer headers
    cf_viewer_headers = {
        "CloudFront-Viewer-Country": request.headers.get("CloudFront-Viewer-Country"),
        "CloudFront-Viewer-Country-Region": request.headers.get("CloudFront-Viewer-Country-Region"),
        "CloudFront-Viewer-Http-Version": request.headers.get("CloudFront-Viewer-Http-Version"),
        "CloudFront-Is-Mobile-Viewer": request.headers.get("CloudFront-Is-Mobile-Viewer"),
        "CloudFront-Is-Desktop-Viewer": request.headers.get("CloudFront-Is-Desktop-Viewer"),
    }
    for k, v in cf_viewer_headers.items():
        if v:
            headers[k] = v
    
    # Critical headers for signed URLs
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    headers["Accept-Encoding"] = "gzip, deflate, br"
    headers["Accept"] = "*/*"
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    headers["Accept-Ranges"] = "bytes"
    
    # Add referer for signed URL validation
    origin = request.headers.get("Origin", "https://www.physicswalllah.com")
    headers["Referer"] = origin
    headers["Origin"] = origin
    
    # Range header support
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    
    last_exc = None
    last_status = None
    last_response = None
    
    # ────────────────────────────────────────────────────────────────
    # Smart Retry Strategy
    # ────────────────────────────────────────────────────────────────
    for attempt in range(UPSTREAM_MAX_RETRIES + 1):
        try:
            logger.info(f"[Attempt {attempt + 1}] Fetching: {url[:100]}...")
            
            # Use session for cookie persistence
            response = session.get(
                url,
                headers=headers,
                timeout=UPSTREAM_TIMEOUT,
                allow_redirects=True,
                stream=True,
                verify=True,
                cookies=client_cookies if client_cookies else None
            )
            
            last_response = response
            
            # Log response
            logger.info(f"[Attempt {attempt + 1}] Status: {response.status_code}, Content-Type: {response.headers.get('Content-Type', 'unknown')}")
            
            # Log Set-Cookie (CloudFront auth cookies)
            if response.headers.get("Set-Cookie"):
                logger.info(f"[Auth] Set-Cookie received: {response.headers.get('Set-Cookie')[:80]}...")
            
            # ─── SUCCESS: 2xx ───
            if 200 <= response.status_code < 300:
                logger.info(f"✅ [SUCCESS] Status {response.status_code}")
                return response
            
            # ─── NO AUTH: 401/403 - DO NOT RETRY ───
            if response.status_code in [401, 403]:
                logger.error(f"❌ [AUTH FAILED] Status {response.status_code} - Token/URL expired or invalid")
                try:
                    error_text = response.text[:300]
                    logger.error(f"Error response: {error_text}")
                except:
                    pass
                # Return immediately - retrying won't help
                return response
            
            # ─── OTHER 4xx - DO NOT RETRY ───
            if 400 <= response.status_code < 500:
                logger.warning(f"❌ [CLIENT ERROR] Status {response.status_code} - No retry")
                return response
            
            # ─── SERVER ERROR: 5xx - RETRY ───
            if response.status_code >= 500:
                last_status = response.status_code
                if attempt < UPSTREAM_MAX_RETRIES:
                    backoff = 0.5 * (attempt + 1)
                    logger.warning(f"⚠️ [SERVER ERROR] Status {response.status_code}, retrying in {backoff}s...")
                    time.sleep(backoff)
                    continue
                else:
                    logger.error(f"❌ [FINAL ATTEMPT] Status {response.status_code} - Max retries exhausted")
                    return response
                
        except requests.exceptions.Timeout as e:
            if attempt < UPSTREAM_MAX_RETRIES:
                backoff = 0.5 * (attempt + 1)
                logger.warning(f"⚠️ [TIMEOUT] Attempt {attempt + 1}, retrying in {backoff}s...")
                last_exc = "Timeout"
                time.sleep(backoff)
                continue
            else:
                logger.error(f"❌ [TIMEOUT] Max retries exhausted")
                last_exc = "Timeout"
                break
                
        except requests.exceptions.ConnectionError as e:
            if attempt < UPSTREAM_MAX_RETRIES:
                backoff = 0.5 * (attempt + 1)
                logger.warning(f"⚠️ [CONNECTION ERROR] Attempt {attempt + 1}, retrying in {backoff}s...")
                last_exc = "ConnectionError"
                time.sleep(backoff)
                continue
            else:
                logger.error(f"❌ [CONNECTION ERROR] Max retries exhausted: {e}")
                last_exc = str(e)
                break
                
        except Exception as e:
            logger.error(f"❌ [UNEXPECTED ERROR] {type(e).__name__}: {e}")
            last_exc = str(e)
            break
    
    # ────────────────────────────────────────────────────────────────
    # All attempts exhausted
    # ────────────────────────────────────────────────────────────────
    error_msg = f"Fetch failed after {UPSTREAM_MAX_RETRIES + 1} attempts"
    if last_status:
        error_msg += f" (last status: {last_status})"
    if last_exc:
        error_msg += f" ({last_exc})"
    
    logger.error(f"❌ {error_msg}")
    
    # Return last response if available
    if last_response:
        logger.warning(f"Returning upstream response: {last_response.status_code}")
        return last_response
    
    raise Exception(error_msg)


# ═══════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/pw', methods=['GET', 'HEAD', 'OPTIONS'])
def download_video():
    """
    DOWNLOAD-ONLY endpoint (NO STREAMING).
    - Downloads video/MPD/m3u8 files directly
    - Returns complete file for client to download
    - Handles CloudFront signed URLs with proper auth
    
    Query Parameters:
    - url (required): Complete video/MPD URL
    - token (optional): JWT token for authentication
    """
    
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Get video URL from query params
        video_url = request.args.get('url', '').strip()
        
        if not video_url:
            logger.warning("No URL provided to /pw endpoint")
            return jsonify({
                "error": "Missing 'url' query parameter",
                "example": "/pw?url=https://domain.com/video.mpd&token=xyz"
            }), 400
        
        # Log request (sanitize for privacy)
        url_preview = video_url[:120] + ("..." if len(video_url) > 120 else "")
        logger.info(f"📥 DOWNLOAD request: {url_preview}")
        
        # Detect file type
        file_ext = ""
        if ".mpd" in video_url.lower():
            file_ext = "mpd"
            file_type = "DASH Manifest"
        elif ".m3u8" in video_url.lower() or "playlist" in video_url.lower():
            file_ext = "m3u8"
            file_type = "HLS Playlist"
        else:
            file_ext = "bin"
            file_type = "Video/Binary"
        
        logger.info(f"File type detected: {file_type} ({file_ext})")
        
        # Extract token from URL or query params
        token = request.args.get('token', '').strip()
        if not token:
            token = extract_token_from_url(video_url)
        
        if token:
            logger.info(f"✔ Token auth detected (length: {len(token)})")
        
        # Fetch upstream with retry
        try:
            logger.info("📡 Fetching from upstream...")
            upstream = fetch_upstream_with_retry(video_url, token)
            logger.info(f"✔ Upstream response received: {upstream.status_code}")
        except Exception as e:
            logger.error(f"❌ Upstream fetch failed: {e}", exc_info=True)
            return jsonify({
                "error": "Failed to fetch from upstream",
                "details": str(e),
                "type": type(e).__name__
            }), 502
        
        # Handle upstream errors
        if upstream.status_code >= 400:
            logger.error(f"❌ Upstream error: {upstream.status_code}")
            
            # Special handling for CloudFront 403/401
            if upstream.status_code in [401, 403]:
                logger.error("⚠️ CLOUDFRONT AUTH FAILED")
                logger.error(f"  Reason: Token expired, Key-Pair-ID missing, or signature invalid")
            
            # Log error content
            try:
                error_text = upstream.text[:300]
                logger.error(f"  Response: {error_text}")
            except:
                pass
            
            # Return upstream error to client
            return upstream.content, upstream.status_code, dict(upstream.headers)
        
        # ✅ SUCCESS - Download mode (not streaming)
        logger.info(f"✅ Downloading {file_type}...")
        
        # Read entire content (this is DOWNLOAD, not stream)
        try:
            content = upstream.content
            content_length = len(content)
            logger.info(f"✅ Downloaded {content_length} bytes")
        except Exception as e:
            logger.error(f"❌ Error reading content: {e}")
            return jsonify({
                "error": "Failed to read file content",
                "details": str(e)
            }), 500
        
        # Prepare response headers
        response_headers = dict(upstream.headers)
        
        # CORS headers
        response_headers["Access-Control-Allow-Origin"] = "*"
        response_headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response_headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Type, Content-Disposition"
        
        # Force download (not inline display)
        response_headers["Content-Disposition"] = f'attachment; filename="video.{file_ext}"'
        response_headers["Content-Length"] = str(content_length)
        
        # Remove streaming headers (not needed for download)
        response_headers.pop("Content-Range", None)
        response_headers.pop("Accept-Ranges", None)
        
        # Remove sensitive headers
        for header in ["Server", "X-Amzn-RequestId", "X-Cache", "Via"]:
            response_headers.pop(header, None)
        
        # Set proper content type
        if file_ext == "mpd":
            content_type = "application/dash+xml"
        elif file_ext == "m3u8":
            content_type = "application/vnd.apple.mpegurl"
        else:
            content_type = "video/mp4"
        
        response_headers["Content-Type"] = content_type
        
        logger.info(f"📥 Sending file download: {content_length} bytes as {content_type}")
        
        # Return complete file (download mode)
        return Response(
            content,
            status=200,
            headers=response_headers,
            mimetype=content_type
        )
    
    except Exception as e:
        logger.error(f"❌ Unhandled error: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "details": str(e),
            "type": type(e).__name__
        }), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for monitoring."""
    return jsonify({
        "status": "healthy",
        "service": "MyProjects MS Video API",
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API documentation."""
    return jsonify({
        "service": "MyProjects MS - Video Download/Stream API",
        "version": "1.0.0",
        "endpoints": {
            "download": {
                "path": "/pw",
                "method": "GET",
                "description": "Stream/download video content",
                "parameters": {
                    "url": {
                        "type": "string",
                        "required": True,
                        "description": "Complete video URL (MPD, M3U8, etc.) with optional token"
                    },
                    "token": {
                        "type": "string",
                        "required": False,
                        "description": "JWT token (if not in URL)"
                    }
                },
                "example": "/pw?url=https://cdn.example.com/video.mpd&parentId=123&childId=456&token=eyJ..."
            },
            "health": {
                "path": "/health",
                "method": "GET",
                "description": "API health check"
            }
        },
        "features": [
            "CORS enabled (all origins)",
            "Token-based authentication",
            "Retry logic for transient failures",
            "Range request support",
            "Stream chunking for large files"
        ]
    }), 200


# ═══════════════════════════════════════════════════════════════════════════
# ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found", "path": request.path}), 404


@app.errorhandler(500)
def server_error(error):
    logger.error(f"Server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting MyProjects MS Video API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
