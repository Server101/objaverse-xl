"""Thingiverse support for Objaverse-XL."""

import multiprocessing
import os
import tempfile
from multiprocessing import Pool
from typing import Callable, Dict, Optional, Tuple

import fsspec
import pandas as pd
import requests
from loguru import logger
from tqdm import tqdm

from objaverse.utils import get_file_hash, get_uid_from_str
from objaverse.xl.abstract import ObjaverseSource


class ThingiverseDownloader(ObjaverseSource):
    """A downloader for Thingiverse objects in Objaverse-XL."""

    @classmethod
    def _get_annotations(
        cls,
        url: str,
        filename: str,
        download_dir: str,
        refresh: bool,
    ) -> pd.DataFrame:
        download_path = os.path.join(download_dir, "thingiverse", filename)
        fs, path = fsspec.core.url_to_fs(download_path)

        if refresh or not fs.exists(path):
            fs.makedirs(os.path.dirname(path), exist_ok=True)
            logger.info(f"Downloading {url} to {download_path}")
            response = requests.get(url)
            response.raise_for_status()
            with fs.open(path, "wb") as file:
                file.write(response.content)

        with fs.open(download_path, "rb") as file:
            annotations_df = pd.read_parquet(file)

        if "metadata" not in annotations_df.columns:
            annotations_df["metadata"] = "{}"

        return annotations_df

    @classmethod
    def get_annotations(
        cls, download_dir: str = "~/.objaverse", refresh: bool = False
    ) -> pd.DataFrame:
        return cls._get_annotations(
            url="https://huggingface.co/datasets/allenai/objaverse-xl/resolve/main/thingiverse/thingiverse.parquet",
            filename="thingiverse.parquet",
            download_dir=download_dir,
            refresh=refresh,
        )

    @classmethod
    def get_alignment_annotations(
        cls, download_dir: str = "~/.objaverse", refresh: bool = False
    ) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["fileIdentifier", "source", "license", "fileType", "sha256", "metadata"]
        )

    @classmethod
    def _download_object(
        cls,
        file_identifier: str,
        download_dir: Optional[str],
        expected_sha256: str,
        handle_found_object: Optional[Callable] = None,
        handle_modified_object: Optional[Callable] = None,
        handle_missing_object: Optional[Callable] = None,
    ) -> Tuple[str, Optional[str]]:
        uid = get_uid_from_str(file_identifier)
        file_extension = os.path.splitext(file_identifier.split("?")[0])[1] or ".object"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, f"{uid}{file_extension}")
            temp_path_tmp = f"{temp_path}.tmp"
            response = requests.get(file_identifier, stream=True)

            if response.status_code == 404:
                logger.warning(f"404 for {file_identifier}")
                if handle_missing_object is not None:
                    handle_missing_object(
                        file_identifier=file_identifier,
                        sha256=expected_sha256,
                        metadata={},
                    )
                return file_identifier, None

            response.raise_for_status()
            with open(temp_path_tmp, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            os.rename(temp_path_tmp, temp_path)

            sha256 = get_file_hash(temp_path)
            if sha256 == expected_sha256:
                if handle_found_object is not None:
                    handle_found_object(
                        local_path=temp_path,
                        file_identifier=file_identifier,
                        sha256=sha256,
                        metadata={},
                    )
            elif handle_modified_object is not None:
                handle_modified_object(
                    local_path=temp_path,
                    file_identifier=file_identifier,
                    new_sha256=sha256,
                    old_sha256=expected_sha256,
                    metadata={},
                )

            if download_dir is not None:
                filename = os.path.join(
                    download_dir, "thingiverse", "objects", f"{uid}{file_extension}"
                )
                fs, path = fsspec.core.url_to_fs(filename)
                fs.makedirs(os.path.dirname(path), exist_ok=True)
                fs.put(temp_path, path)
            else:
                path = None

        return file_identifier, path

    @classmethod
    def _parallel_download_object(cls, args):
        return cls._download_object(*args)

    @classmethod
    def download_objects(
        cls,
        objects: pd.DataFrame,
        download_dir: Optional[str] = "~/.objaverse",
        processes: Optional[int] = None,
        handle_found_object: Optional[Callable] = None,
        handle_modified_object: Optional[Callable] = None,
        handle_missing_object: Optional[Callable] = None,
        **kwargs,
    ) -> Dict[str, str]:
        if processes is None:
            processes = multiprocessing.cpu_count()

        out = {}
        objects_to_download = []
        if download_dir is not None:
            objects_dir = os.path.join(download_dir, "thingiverse", "objects")
            fs, path = fsspec.core.url_to_fs(objects_dir)
            fs.makedirs(path, exist_ok=True)
            existing_files = fs.glob(os.path.join(objects_dir, "*"), refresh=True)
            existing_uids = {
                os.path.basename(file).split(".")[0]
                for file in existing_files
                if not file.endswith(".tmp")
            }

            for _, item in objects.iterrows():
                file_identifier = item["fileIdentifier"]
                uid = get_uid_from_str(file_identifier)
                if uid in existing_uids:
                    matching_paths = [file for file in existing_files if os.path.basename(file).startswith(uid)]
                    if matching_paths:
                        out[file_identifier] = matching_paths[0]
                else:
                    objects_to_download.append(item)
        else:
            objects_to_download = [item for _, item in objects.iterrows()]

        logger.info(
            f"Downloading {len(objects_to_download)} Thingiverse objects with {processes} processes"
        )

        if len(objects_to_download) == 0:
            return out

        args = [
            [
                item["fileIdentifier"],
                download_dir,
                item["sha256"],
                handle_found_object,
                handle_modified_object,
                handle_missing_object,
            ]
            for item in objects_to_download
        ]
        with Pool(processes=processes) as pool:
            results = list(
                tqdm(
                    pool.imap_unordered(cls._parallel_download_object, args),
                    total=len(objects_to_download),
                    desc="Downloading Thingiverse objects",
                )
            )

        for file_identifier, download_path in results:
            if download_path is not None:
                out[file_identifier] = download_path

        return out
