from __future__ import annotations

import argparse
from pathlib import Path

import AppKit  # type: ignore[import-not-found]


def render(source: Path, destination: Path, size: int) -> None:
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(source))
    if image is None:
        raise RuntimeError(f"macOS could not read {source}")
    image.setSize_(AppKit.NSMakeSize(size, size))
    representation = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None,
        size,
        size,
        8,
        4,
        True,
        False,
        AppKit.NSCalibratedRGBColorSpace,
        0,
        0,
    )
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(representation)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(context)
    image.drawInRect_fromRect_operation_fraction_(
        AppKit.NSMakeRect(0, 0, size, size),
        AppKit.NSMakeRect(0, 0, image.size().width, image.size().height),
        AppKit.NSCompositingOperationSourceOver,
        1.0,
    )
    context.flushGraphics()
    AppKit.NSGraphicsContext.restoreGraphicsState()
    data = representation.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not data.writeToFile_atomically_(str(destination), True):
        raise RuntimeError(f"Could not write {destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("size", type=int)
    arguments = parser.parse_args()
    render(arguments.source, arguments.destination, arguments.size)


if __name__ == "__main__":
    main()
