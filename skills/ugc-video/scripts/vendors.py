import http.client
import json
import mimetypes
import os
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

FAL_BASE = "https://queue.fal.run"
FAL_REST = "https://rest.fal.ai"
KIE_BASE = "https://api.kie.ai"
REFUSED = (429,)
KIE_REFUSED = (429, 455)
UNANSWERED = (OSError, http.client.HTTPException, ValueError)
SCALE = float(os.environ.get("UGC_RETRY_SCALE") or 1)


class VendorError(Exception):
    pass


class TakeFailed(VendorError):
    pass


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(RefuseRedirect)


def log(msg):
    print(msg, flush=True)


def call(method, url, headers, body=None):
    data = None if body is None else json.dumps(body).encode()
    with OPENER.open(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=60) as reply:
        result = json.loads(reply.read())
    if not isinstance(result, dict):
        raise ValueError("the reply is not a JSON object")
    return result


def http_error(error):
    try:
        text = " ".join(error.read().decode("utf-8", "replace").split())
    except (OSError, http.client.HTTPException):
        text = ""
    return f"HTTP {error.code} {text[:300]}".rstrip()


def submit(vendor, url, headers, body, refused):
    for attempt in range(3):
        try:
            reply = call("POST", url, headers, body)
        except urllib.error.HTTPError as error:
            if error.code >= 500:
                raise VendorError(f"{vendor} submit got {http_error(error)}. Not resubmitted, "
                                  "because the job may have been accepted") from None
            if error.code not in REFUSED or attempt == 2:
                raise VendorError(f"{vendor} refused the submit: {http_error(error)}") from None
            reason = f"HTTP {error.code}"
        except UNANSWERED as error:
            raise VendorError(f"{vendor} submit got no clear answer ({error}). Not resubmitted, "
                              "because the job may have been accepted") from None
        else:
            reason = refused(reply)
            if not reason or attempt == 2:
                return reply
        wait = 2 ** (attempt + 1)
        log(f"  {vendor} refused the submit ({reason}), retrying in {wait}s")
        time.sleep(wait * SCALE)


def poll(url, headers, deadline, job, done):
    wait = 1
    while True:
        try:
            reply = call("GET", url, headers)
        except urllib.error.HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise VendorError(f"{job}: {http_error(error)}") from None
            reply = None
        except UNANSWERED:
            reply = None
        result = None if reply is None else done(reply)
        if result is not None:
            return result
        if time.monotonic() + wait * SCALE > deadline:
            raise VendorError(f"{job} did not finish in time. Not resubmitted")
        time.sleep(wait * SCALE)
        wait = min(wait * 2, 15)


def fal_failure(job, reply):
    return VendorError(f"{job} failed: {reply.get('error_type') or reply.get('status') or 'error'}: {reply.get('error')}")


def fal_run(model, body, *, key, base=FAL_BASE, timeout=1800):
    base = base.rstrip("/")
    auth = {"Authorization": f"Key {key}"}
    deadline = time.monotonic() + timeout
    job = submit("fal", f"{base}/{model}", {**auth, "Content-Type": "application/json"}, body, lambda reply: None)
    name = f"fal request {job.get('request_id')}"
    for url in (job.get("status_url"), job.get("response_url")):
        if not isinstance(url, str) or not url.startswith(base + "/"):
            raise VendorError(f"{name}: not polling {url!r}, which is outside {base}")

    def completed(status):
        if status.get("status") in ("IN_QUEUE", "IN_PROGRESS"):
            return None
        if status.get("status") != "COMPLETED" or status.get("error"):
            raise fal_failure(name, status)
        return status

    poll(job["status_url"], auth, deadline, name, completed)
    result = poll(job["response_url"], auth, deadline, name, lambda reply: reply)
    if result.get("error"):
        raise fal_failure(name, result)
    return result


def kie_submit(model, input, *, key, base=KIE_BASE):
    reply = submit("kie", f"{base.rstrip('/')}/api/v1/jobs/createTask",
                   {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   {"model": model, "input": input},
                   lambda reply: f"code {reply.get('code')}" if reply.get("code") in KIE_REFUSED else None)
    task = (reply.get("data") or {}).get("taskId")
    if reply.get("code") != 200 or not isinstance(task, str) or not task:
        raise VendorError(f"kie refused the task: code {reply.get('code')}: {reply.get('msg')}")
    return task


def kie_wait(task, *, key, base=KIE_BASE, timeout=1800):
    base = base.rstrip("/")
    auth = {"Authorization": f"Bearer {key}"}
    deadline = time.monotonic() + timeout
    name = f"kie task {task}"

    def finished(reply):
        code = reply.get("code")
        if code in KIE_REFUSED or (isinstance(code, int) and code >= 500):
            return None
        if code != 200:
            raise VendorError(f"{name}: code {code}: {reply.get('msg')}")
        record = reply.get("data") or {}
        state = record.get("state")
        if state in ("waiting", "queuing", "generating"):
            return None
        if state == "fail":
            raise TakeFailed(f"{name} failed: {record.get('failCode')}: {record.get('failMsg')}")
        if state != "success":
            raise VendorError(f"{name}: unknown state {state!r}")
        try:
            record["result"] = json.loads(record.get("resultJson") or "")
        except ValueError:
            raise VendorError(f"{name} succeeded, but its resultJson is not JSON") from None
        return record

    query = urllib.parse.urlencode({"taskId": task})
    return poll(f"{base}/api/v1/jobs/recordInfo?{query}", auth, deadline, name, finished)


def fal_upload(path, name, *, key, rest=FAL_REST):
    path = Path(path)
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        target = call("POST", f"{rest.rstrip('/')}/storage/upload/initiate?storage_type=fal-cdn-v3",
                      {"Authorization": f"Key {key}", "Content-Type": "application/json"},
                      {"file_name": name, "content_type": kind})
    except urllib.error.HTTPError as error:
        raise VendorError(f"fal storage refused {name}: {http_error(error)}") from None
    except UNANSWERED as error:
        raise VendorError(f"fal storage did not answer for {name}: {error}") from None
    put, url = target.get("upload_url"), target.get("file_url")
    for link in (put, url):
        if not isinstance(link, str) or not link.startswith(("https://", "http://")):
            raise VendorError(f"fal storage gave no usable upload_url and file_url for {name}")
    request = urllib.request.Request(put, data=path.read_bytes(), method="PUT", headers={"Content-Type": kind})
    try:
        with OPENER.open(request, timeout=300) as reply:
            reply.read()
    except urllib.error.HTTPError as error:
        raise VendorError(f"fal storage upload of {name} failed: {http_error(error)}") from None
    except UNANSWERED as error:
        raise VendorError(f"fal storage upload of {name} failed: {error}") from None
    return url


def fetch(url, dest):
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise VendorError(f"not downloading {url!r}, which is not an http or https URL")
    dest = Path(dest)
    partial = dest.with_name(dest.name + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=300) as reply, open(partial, "wb") as out:
            shutil.copyfileobj(reply, out)
            expected = reply.headers.get("Content-Length")
        if expected is not None and partial.stat().st_size != int(expected):
            raise VendorError(f"download stopped at {partial.stat().st_size} of {expected} bytes")
    except (OSError, http.client.HTTPException, ValueError, VendorError) as error:
        partial.unlink(missing_ok=True)
        raise VendorError(f"download failed: {error}") from None
    partial.replace(dest)
