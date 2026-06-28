import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.append(str(Path(__file__).parent.parent))

from pipeline.ingestion.download_sentinel2 import (
    convert_raster_to_geotiff,
    resolve_asset_download_href,
)


class FakeAsset:
    def __init__(self, href, extra_fields=None):
        self.href = href
        self.extra_fields = extra_fields or {}


def test_resolve_asset_download_href_prefers_alternate_https():
    asset = FakeAsset(
        href="s3://eodata/Sentinel-2/example.jp2",
        extra_fields={
            "alternate": {
                "https": {
                    "href": "https://download.dataspace.copernicus.eu/odata/v1/Products(example)/$value"
                }
            }
        },
    )

    assert resolve_asset_download_href(asset) == "https://download.dataspace.copernicus.eu/odata/v1/Products(example)/$value"


def test_resolve_asset_download_href_falls_back_to_https_s3_mapping():
    asset = FakeAsset(href="s3://eodata/Sentinel-2/example.jp2")

    assert resolve_asset_download_href(asset) == "https://eodata.dataspace.copernicus.eu/eodata/Sentinel-2/example.jp2"


def test_convert_raster_to_geotiff_writes_gtiff_output(tmp_path):
    source_path = tmp_path / "source.tif"
    output_path = tmp_path / "output.tif"
    data = np.arange(25, dtype=np.uint16).reshape(5, 5)

    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        height=5,
        width=5,
        count=1,
        dtype=data.dtype,
        crs="EPSG:4326",
        transform=from_origin(100.0, 17.0, 0.001, 0.001),
    ) as dst:
        dst.write(data, 1)

    convert_raster_to_geotiff(source_path, output_path)

    assert output_path.exists()
    with rasterio.open(output_path) as dataset:
        assert dataset.driver == "GTiff"
        assert dataset.read(1).shape == (5, 5)
