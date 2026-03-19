# Copyright (c) 2025, Orgatek and contributors
# License: MIT. See LICENSE

"""
Optional file storage: Local (default) or S3.
Configure via site config or "File Storage Settings" Single DocType.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import frappe
from frappe.utils.file_manager import save_file_on_filesystem

if TYPE_CHECKING:
	from frappe.core.doctype.file.file import File


def get_storage_backend() -> str:
	"""Return 'local' or 's3' from File Storage Settings or site config."""
	try:
		settings = frappe.get_single("File Storage Settings")
		if settings and settings.storage_backend:
			return (settings.storage_backend or "local").lower().strip()
	except Exception:
		pass
	backend = (frappe.conf.get("file_storage_backend") or "local").lower().strip()
	return backend if backend in ("local", "s3") else "local"


def _get_s3_config() -> dict[str, Any] | None:
	"""Get S3 config from File Storage Settings or site config. Returns None if not configured."""
	try:
		settings = frappe.get_single("File Storage Settings")
		if settings and settings.storage_backend and (settings.storage_backend or "").lower() == "s3":
			secret = settings.get_password("aws_secret_access_key") if hasattr(settings, "get_password") else (settings.aws_secret_access_key or "")
			return {
				"bucket": settings.s3_bucket or "",
				"region": settings.region_name or None,
				"access_key": settings.aws_access_key_id or "",
				"secret_key": secret,
				"endpoint_url": settings.endpoint_url or None,
				"path_prefix": (settings.s3_path_prefix or "").strip().rstrip("/") or None,
			}
	except Exception:
		pass
	bucket = frappe.conf.get("file_storage_s3_bucket")
	if not bucket:
		return None
	return {
		"bucket": bucket,
		"region": frappe.conf.get("file_storage_s3_region"),
		"access_key": frappe.conf.get("file_storage_s3_access_key_id") or "",
		"secret_key": frappe.conf.get("file_storage_s3_secret_access_key") or "",
		"endpoint_url": frappe.conf.get("file_storage_s3_endpoint_url"),
		"path_prefix": frappe.conf.get("file_storage_s3_path_prefix") or None,
	}


def write_file(
	fname_or_doc: str | File,
	content: bytes | None = None,
	content_type: str | None = None,
	is_private: int = 0,
) -> dict[str, str] | None:
	"""
	Frappe hook: write file to local filesystem or S3.
	Called with (fname, content, content_type, is_private) from file_manager.save_file,
	or with (self) from File.save_file (doc).
	Returns {"file_name": ..., "file_url": ...} for file_manager path; for File doc may set doc.file_url and return.
	"""
	# Call from File doc: write_file_method(self)
	if content is None and hasattr(fname_or_doc, "file_name"):
		doc = fname_or_doc
		content = getattr(doc, "_content", None) or b""
		fname = doc.file_name or "file"
		is_private = 1 if doc.is_private else 0
		content_type = getattr(doc, "content_type", None)
	else:
		doc = None
		fname = fname_or_doc if isinstance(fname_or_doc, str) else "file"
		content = content or b""

	if get_storage_backend() != "s3":
		if doc is None:
			return save_file_on_filesystem(fname, content, content_type=content_type, is_private=is_private)
		# File doc path: Frappe expects hook to handle; fall back to default by not registering for doc path
		# Actually when we register write_file, File doc calls it with (self). So we must handle it.
		# Frappe's File.save_file does: write_file_method(self); if hook returns, use it; else save_file_on_filesystem().
		# So we must return the same dict. For local we can call the default implementation on the doc.
		if doc is not None:
			return _write_file_local_doc(doc)
		return save_file_on_filesystem(fname, content, content_type=content_type, is_private=is_private)

	# S3 path
	s3_config = _get_s3_config()
	if not s3_config or not s3_config.get("bucket"):
		if doc is None:
			return save_file_on_filesystem(fname, content, content_type=content_type, is_private=is_private)
		return _write_file_local_doc(doc)

	if doc is not None:
		return _write_file_s3_doc(doc, s3_config)
	return _write_file_s3(fname, content, content_type, is_private, s3_config)


def _write_file_local_doc(doc: "File") -> dict[str, str]:
	"""Write File doc to local filesystem (Frappe default)."""
	import re
	from frappe.utils.file_manager import write_file as frappe_write_file

	safe_file_name = re.sub(r"[/\\%?#]", "_", doc.file_name or "file")
	fpath = frappe_write_file(doc._content or b"", safe_file_name, doc.is_private)
	if doc.is_private:
		doc.file_url = f"/private/files/{safe_file_name}"
	else:
		doc.file_url = f"/files/{safe_file_name}"
	return {"file_name": safe_file_name, "file_url": doc.file_url}


def _write_file_s3(
	fname: str,
	content: bytes,
	content_type: str | None,
	is_private: int,
	config: dict,
) -> dict[str, str]:
	"""Upload bytes to S3 and return file_name and file_url."""
	import boto3
	from botocore.exceptions import ClientError

	key = fname
	if config.get("path_prefix"):
		key = f"{config['path_prefix']}/{fname}"

	client = boto3.client(
		"s3",
		aws_access_key_id=config.get("access_key") or None,
		aws_secret_access_key=config.get("secret_key") or None,
		region_name=config.get("region"),
		endpoint_url=config.get("endpoint_url"),
	)
	try:
		extra = {}
		if content_type:
			extra["ContentType"] = content_type
		client.put_object(Bucket=config["bucket"], Key=key, Body=content, **extra)
	except ClientError as e:
		frappe.throw(
			frappe._("Failed to upload file to S3: {0}").format(str(e)),
			exc=OSError,
		)

	# Public URL: standard S3 format (works if bucket/object is public; else use presigned in frontend later)
	endpoint = (config.get("endpoint_url") or "").rstrip("/")
	if endpoint:
		# Custom endpoint (e.g. MinIO): https://minio.example.com -> https://minio.example.com/bucket/key
		file_url = f"{endpoint}/{config['bucket']}/{key}"
	else:
		# AWS: https://bucket.s3.region.amazonaws.com/key or https://s3.region.amazonaws.com/bucket/key
		region = config.get("region") or "us-east-1"
		file_url = f"https://{config['bucket']}.s3.{region}.amazonaws.com/{key}"

	return {"file_name": os.path.basename(fname), "file_url": file_url}


def _write_file_s3_doc(doc: "File", config: dict) -> dict[str, str]:
	"""Upload File doc content to S3 and set doc.file_url."""
	result = _write_file_s3(
		doc.file_name or "file",
		doc._content or b"",
		getattr(doc, "content_type", None),
		1 if doc.is_private else 0,
		config,
	)
	doc.file_url = result["file_url"]
	return result


def delete_file_data_content(doc: "File", only_thumbnail: bool = False) -> None:
	"""
	Frappe hook: delete file content from storage when File doc is deleted.
	For local, Frappe default deletes from disk. For S3, delete object.
	"""
	if only_thumbnail:
		# Thumbnail handling if needed
		from frappe.utils.file_manager import delete_file_from_filesystem
		delete_file_from_filesystem(doc, only_thumbnail=only_thumbnail)
		return

	if get_storage_backend() != "s3":
		from frappe.utils.file_manager import delete_file_from_filesystem
		delete_file_from_filesystem(doc, only_thumbnail=only_thumbnail)
		return

	file_url = (doc.file_url or "").strip()
	if not file_url or not (file_url.startswith("http://") or file_url.startswith("https://")):
		# Local path or no URL
		from frappe.utils.file_manager import delete_file_from_filesystem
		delete_file_from_filesystem(doc, only_thumbnail=only_thumbnail)
		return

	# Parse S3 URL to get bucket and key, then delete object
	s3_config = _get_s3_config()
	if not s3_config:
		return

	# Key from URL: .../bucket/key or .../key (endpoint style)
	endpoint = (s3_config.get("endpoint_url") or "").rstrip("/")
	bucket = s3_config["bucket"]
	if endpoint and file_url.startswith(endpoint):
		# endpoint/bucket/key
		prefix = f"{endpoint}/{bucket}/"
		if file_url.startswith(prefix):
			key = file_url[len(prefix):].lstrip("/")
		else:
			return
	else:
		# AWS URL: https://bucket.s3.region.amazonaws.com/key
		prefix = f"https://{bucket}.s3."
		if file_url.startswith(prefix):
			parts = file_url.split(".amazonaws.com/", 1)
			key = parts[1] if len(parts) == 2 else ""
		else:
			return

	if not key:
		return

	import boto3
	from botocore.exceptions import ClientError

	client = boto3.client(
		"s3",
		aws_access_key_id=s3_config.get("access_key") or None,
		aws_secret_access_key=s3_config.get("secret_key") or None,
		region_name=s3_config.get("region"),
		endpoint_url=s3_config.get("endpoint_url"),
	)
	try:
		client.delete_object(Bucket=bucket, Key=key)
	except ClientError:
		pass  # Log but do not fail doc delete
