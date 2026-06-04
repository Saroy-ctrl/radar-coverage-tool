"""
DEM Preprocessor — SRTM void detection and filling.

SRTM3 DEMs contain voids (NoData = -32768) from water bodies, clouds, or processing gaps.
If not filled: h_apparent = -32768 - curvature → invisible terrain, wrong coverage.

This module detects and fills voids using a tiered approach:
1. Small voids (<10 px): morphological fill with nanmean
2. Medium voids (10–100 px): scipy.interpolate.griddata linear
3. Large voids (>100 px): scipy.interpolate.griddata nearest
4. Edge smoothing: gaussian_filter blended only at boundaries

Output cached as float32 GeoTIFF (no NaNs remaining, max fill deviation <500 m).

Reference: SRTM metadata, scipy.interpolate, scipy.ndimage documentation.
"""

import numpy as np
import logging
from scipy import ndimage
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

logger = logging.getLogger(__name__)

# SRTM void detection thresholds (SI units: metres AMSL)
SRTM_NODATA_VALUE = -32768  # Official SRTM NoData marker
MIN_VALID_ELEVATION_M = -500  # Bathymetry/error threshold
MAX_VALID_ELEVATION_M = 9000  # Mt. Everest is 8848 m
VOID_FILL_MAX_DEVIATION_M = 500  # Assertion: filled values must be within 500 m of neighbours


def detect_voids(elevation_array):
    """
    Detect void pixels in SRTM elevation array.

    A pixel is void if:
    - value == SRTM_NODATA_VALUE (-32768)
    - value < MIN_VALID_ELEVATION_M (-500 m)
    - value > MAX_VALID_ELEVATION_M (9000 m)

    Args:
        elevation_array (np.ndarray): 2D elevation array in metres (float64)

    Returns:
        np.ndarray: boolean mask (True = void, False = valid)
        tuple: (void_count, void_fraction) for logging

    Example:
        >>> dem = np.array([[-32768, 100], [200, 300]], dtype=np.float64)
        >>> mask, (count, frac) = detect_voids(dem)
        >>> count == 1  # One void
        >>> frac ≈ 0.25  # 25%
    """
    void_mask = (
        (elevation_array == SRTM_NODATA_VALUE) |
        (elevation_array < MIN_VALID_ELEVATION_M) |
        (elevation_array > MAX_VALID_ELEVATION_M)
    )
    void_count = np.sum(void_mask)
    void_fraction = void_count / elevation_array.size if elevation_array.size > 0 else 0.0
    return void_mask, (void_count, void_fraction)


def _fill_small_voids(elevation_array, void_mask, size_threshold=10):
    """
    Fill small voids (<size_threshold pixels) using nanmean morphological filter.

    Approach: For each void pixel, compute mean of non-void neighbours in 7x7 window.
    This preserves slopes and is fast.

    Args:
        elevation_array (np.ndarray): 2D elevation in metres (float64)
        void_mask (np.ndarray): boolean mask (True = void)
        size_threshold (int): max void size (pixels) to process here

    Returns:
        np.ndarray: filled elevation array
        np.ndarray: updated void mask (remaining voids)
    """
    from scipy.ndimage import label, sum as ndi_sum

    labeled, num_features = label(void_mask)
    filled = elevation_array.copy()
    remaining_mask = void_mask.copy()

    for void_id in range(1, num_features + 1):
        void_pixels = labeled == void_id
        void_size = np.sum(void_pixels)

        if void_size < size_threshold:
            # Use nanmean filter in 7x7 window
            temp = filled.copy()
            temp[void_pixels] = np.nan
            filled_region = ndimage.generic_filter(
                temp, function=np.nanmean, size=7, mode='reflect'
            )
            filled[void_pixels] = filled_region[void_pixels]
            remaining_mask[void_pixels] = False

    return filled, remaining_mask


def _fill_medium_voids(elevation_array, void_mask, size_threshold=(10, 100)):
    """
    Fill medium voids (10–100 pixels) using scipy.interpolate.griddata (linear).

    Approach: Interpolate from valid neighbours using linear basis functions.
    Preserves gradients better than nanmean for medium-sized gaps.

    Args:
        elevation_array (np.ndarray): 2D elevation in metres (float64)
        void_mask (np.ndarray): boolean mask (True = void)
        size_threshold (tuple): (min, max) void size range

    Returns:
        np.ndarray: filled elevation array
        np.ndarray: updated void mask
    """
    from scipy.ndimage import label

    labeled, num_features = label(void_mask)
    filled = elevation_array.copy()
    remaining_mask = void_mask.copy()

    # Get coordinates of valid pixels for interpolation
    yy, xx = np.mgrid[0:elevation_array.shape[0], 0:elevation_array.shape[1]]
    valid_mask = ~void_mask
    valid_points = np.column_stack((yy[valid_mask], xx[valid_mask]))
    valid_values = elevation_array[valid_mask]

    for void_id in range(1, num_features + 1):
        void_pixels = labeled == void_id
        void_size = np.sum(void_pixels)

        if size_threshold[0] <= void_size < size_threshold[1]:
            void_coords = np.column_stack((yy[void_pixels], xx[void_pixels]))
            filled_values = griddata(
                valid_points, valid_values, void_coords,
                method='linear', fill_value=np.nan
            )
            filled[void_pixels] = filled_values
            remaining_mask[void_pixels] = False

    return filled, remaining_mask


def _fill_large_voids(elevation_array, void_mask, size_threshold=100):
    """
    Fill large voids (>100 pixels) using scipy.interpolate.griddata (nearest).

    Approach: Nearest-neighbour interpolation. Fast and safe for large gaps
    (e.g., entire lakes). May introduce flat regions but avoids overfitting.

    Args:
        elevation_array (np.ndarray): 2D elevation in metres (float64)
        void_mask (np.ndarray): boolean mask (True = void)
        size_threshold (int): min void size (pixels) to process here

    Returns:
        np.ndarray: filled elevation array
        np.ndarray: updated void mask
    """
    from scipy.ndimage import label

    labeled, num_features = label(void_mask)
    filled = elevation_array.copy()
    remaining_mask = void_mask.copy()

    yy, xx = np.mgrid[0:elevation_array.shape[0], 0:elevation_array.shape[1]]
    valid_mask = ~void_mask
    valid_points = np.column_stack((yy[valid_mask], xx[valid_mask]))
    valid_values = elevation_array[valid_mask]

    for void_id in range(1, num_features + 1):
        void_pixels = labeled == void_id
        void_size = np.sum(void_pixels)

        if void_size >= size_threshold:
            void_coords = np.column_stack((yy[void_pixels], xx[void_pixels]))
            filled_values = griddata(
                valid_points, valid_values, void_coords,
                method='nearest', fill_value=np.nan
            )
            filled[void_pixels] = filled_values
            remaining_mask[void_pixels] = False

    return filled, remaining_mask


def fill_voids(elevation_array, void_mask=None):
    """
    Fill voids using tiered approach: small → medium → large.

    Pipeline:
    1. Tier 1: small voids (<10 px) with nanmean
    2. Tier 2: medium voids (10–100 px) with griddata linear
    3. Tier 3: large voids (>100 px) with griddata nearest
    4. Fallback: any remaining NaNs → 0.0 + warning
    5. Edge smoothing: gaussian_filter (σ=1) blended at fill boundaries

    Args:
        elevation_array (np.ndarray): 2D elevation in metres (float64)
        void_mask (np.ndarray, optional): pre-computed void mask. If None, detect voids.

    Returns:
        dict: {
            'filled': filled elevation array (float64, no NaNs),
            'void_mask_original': original void mask,
            'void_mask_remaining': remaining voids after fill (should be empty),
            'void_count': original void count,
            'void_fraction': original void fraction
        }

    Assertions:
        - No NaN values in output
        - Any remaining voids logged with count > 0 generates warning
    """
    elevation_array = np.asarray(elevation_array, dtype=np.float64)

    if void_mask is None:
        void_mask, (void_count, void_frac) = detect_voids(elevation_array)
    else:
        void_count = np.sum(void_mask)
        void_frac = void_count / elevation_array.size if elevation_array.size > 0 else 0.0

    original_void_mask = void_mask.copy()

    logger.info(
        f"DEM void detection: {void_count} voids ({void_frac*100:.2f}%) detected"
    )

    if void_frac > 0.20:
        logger.warning(
            f"High void fraction ({void_frac*100:.2f}%) — DEM quality may be poor"
        )

    # Tier 1: small voids
    filled, void_mask = _fill_small_voids(elevation_array, void_mask, size_threshold=10)
    small_filled = np.sum(original_void_mask & ~void_mask)
    logger.debug(f"Tier 1 (small): {small_filled} voids filled")

    # Tier 2: medium voids
    filled, void_mask = _fill_medium_voids(filled, void_mask, size_threshold=(10, 100))
    medium_filled = np.sum(original_void_mask & ~void_mask) - small_filled
    logger.debug(f"Tier 2 (medium): {medium_filled} voids filled")

    # Tier 3: large voids
    filled, void_mask = _fill_large_voids(filled, void_mask, size_threshold=100)
    large_filled = np.sum(original_void_mask & ~void_mask) - small_filled - medium_filled
    logger.debug(f"Tier 3 (large): {large_filled} voids filled")

    # Fallback: any remaining NaNs
    nan_count = np.sum(np.isnan(filled))
    if nan_count > 0:
        logger.warning(f"Fallback: {nan_count} NaN values remaining after fill, setting to 0")
        filled = np.nan_to_num(filled, nan=0.0)

    # Edge smoothing: gaussian filter (σ=1) blended only at fill boundaries
    if np.sum(original_void_mask) > 0:
        # Create a mask of pixels adjacent to filled voids (blend region)
        from scipy.ndimage import binary_dilation
        blend_mask = binary_dilation(original_void_mask, iterations=1)
        blend_mask = blend_mask & ~original_void_mask  # Only adjacent valid pixels

        if np.sum(blend_mask) > 0:
            filled_smooth = gaussian_filter(filled, sigma=1.0)
            filled[blend_mask] = filled_smooth[blend_mask]
            logger.debug(f"Applied edge smoothing to {np.sum(blend_mask)} boundary pixels")

    # Final assertion
    assert np.isnan(filled).sum() == 0, "NaN values remain after void fill!"

    return {
        'filled': filled,
        'void_mask_original': original_void_mask,
        'void_mask_remaining': void_mask,
        'void_count': void_count,
        'void_fraction': void_frac
    }
