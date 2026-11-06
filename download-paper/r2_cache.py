"""A private S3-compatible bucket shared by every device, keyed like the local cache.

Off unless PAPER_CACHE_R2_BUCKET and the R2 credentials are set. The bucket must not
have a public URL: it can hold paywalled PDFs. A failure here never fails a download:
the first error is logged and the cache stays off for the rest of the process.
"""

import contextlib
import os
import sys
import tempfile
from urllib.parse import quote, unquote


def log(msg):
    print(msg, file=sys.stderr)


class R2Cache:
    def __init__(self, client, bucket, prefix="paper-cache"):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.off = False

    @classmethod
    def from_keys(cls, keys):
        needed = ("PAPER_CACHE_R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT")
        if not all(keys.get(name) for name in needed):
            return None
        try:
            return cls._from_keys(keys)
        except Exception as error:
            # Optional cache setup must not prevent a successful paper download.
            log(f"R2 cache unavailable: {type(error).__name__}")
            return None

    @classmethod
    def _from_keys(cls, keys):
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3",
            endpoint_url=keys["R2_ENDPOINT"],
            aws_access_key_id=keys["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=keys["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            # R2 rejects the checksum headers newer boto3 versions send by default.
            config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 2},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"),
        )
        return cls(client, keys["PAPER_CACHE_R2_BUCKET"], keys.get("PAPER_CACHE_R2_PREFIX") or "paper-cache")

    def _key(self, name):
        return f"{self.prefix}/{name}"

    def _failed(self, error):
        log(f"R2 cache off for this run: {type(error).__name__}: {error}")
        self.off = True

    @staticmethod
    def _is_missing(error):
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
        return code in {"404", "NoSuchKey", "NotFound"}

    def exists(self, name):
        if self.off:
            return False
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(name))
            return True
        except Exception as error:
            if not self._is_missing(error):
                self._failed(error)
            return False

    def fetch(self, name, dest):
        """Download `name` to `dest` atomically. Returns its metadata, or None if absent."""
        if self.off:
            return None
        folder = os.path.dirname(os.path.abspath(dest))
        part = None
        body = None
        try:
            handle, part = tempfile.mkstemp(dir=folder, prefix="part-r2-")
            os.close(handle)
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(name))
            body = response["Body"]
            with open(part, "wb") as f:
                for chunk in body.iter_chunks(1 << 20):
                    f.write(chunk)
            os.replace(part, dest)
            return {k: unquote(v) for k, v in response.get("Metadata", {}).items()}
        except Exception as error:
            if not self._is_missing(error):
                self._failed(error)
            return None
        finally:
            if body is not None:
                with contextlib.suppress(Exception):
                    body.close()
            if part and os.path.exists(part):
                os.unlink(part)

    def store(self, path, name, overwrite=False, **metadata):
        """Upload a file. Returns True if the object is in the bucket afterwards."""
        if self.off:
            return False
        if not overwrite and self.exists(name):
            return True
        if self.off:
            return False
        content_type = "application/pdf" if name.endswith(".pdf") else "application/xml"
        clean = {k: quote(str(v), safe="/:-_. ") for k, v in metadata.items() if v}
        try:
            with open(path, "rb") as f:
                self.client.put_object(Bucket=self.bucket, Key=self._key(name), Body=f,
                                       ContentType=content_type, Metadata=clean,
                                       **({} if overwrite else {"IfNoneMatch": "*"}))
            return True
        except Exception as error:
            if not overwrite and getattr(error, "response", {}).get("Error", {}).get("Code") in {
                "PreconditionFailed", "412",
            }:
                return True  # Another writer stored the object after our existence check.
            self._failed(error)
            return False
