"""
Application for images registration
"""
import os
import shutil
from pathlib import Path
import json
from tkinter import filedialog
from tempfile import gettempdir
import random
import numpy as np
import imageio.v3 as iio
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle
from pystackreg import StackReg
from skimage.transform import warp, estimate_transform

from images_alignment.utils import (Terminal, fnames_multiframes_from_list,
                                    gray_conversion, imgs_conversion,
                                    rescaling_factors, imgs_rescaling,
                                    image_normalization, absolute_threshold,
                                    resizing, cropping, padding, sift,
                                    concatenate_images, rescaling)

TMP_DIR = Path(gettempdir()) / "images_alignment"
shutil.rmtree(TMP_DIR, ignore_errors=True)
os.makedirs(TMP_DIR, exist_ok=True)

REG_MODELS = ['StackReg', 'SIFT', 'SIFT + StackReg', 'User-Driven']
STREG = StackReg(StackReg.AFFINE)
CMAP_BINARIZED = ListedColormap(["#00FF00", "black", "red"])
KEYS = ['rois', 'thresholds', 'bin_inversions', 'registration_model']

plt.rcParams['axes.titlesize'] = 10


class ImagesAlign:
    """
    Application dedicated to images alignment

    Parameters
    ----------
    fnames: iterable of 2 str, optional
        Images pathnames to handle
    thresholds: iterable of 2 floats, optional
        Thresholds used to binarize the images
    bin_inversions: iterable of 2 bools, optional
        Activation keywords to reverse the image binarization
    """

    def __init__(self, fnames_fixed=None, fnames_moving=None, rois=None,
                 thresholds=None, bin_inversions=None, terminal=None):

        if fnames_fixed is not None:
            fnames_fixed = fnames_multiframes_from_list(fnames_fixed)
        if fnames_fixed is not None:
            fnames_moving = fnames_multiframes_from_list(fnames_moving)

        self.fnames_tot = [fnames_fixed, fnames_moving]
        self.fnames = [None, None]
        self.rois = rois or [None, None]
        self.angles = [0, 0]
        self.thresholds = thresholds or [0.5, 0.5]
        self.bin_inversions = bin_inversions or [False, False]
        self.terminal = terminal or Terminal()

        self.binarized = False
        self.mode = 'Juxtaposed'
        self.resolution = 0.
        self.min_img_res = 256
        self.rfactors_plotting = [1., 1.]
        self.juxt_alignment = 'horizontal'

        self.imgs = [None, None]
        self.dtypes = [None, None]
        self.imgs_bin = [None, None]
        self.registration_model = 'StackReg'
        self.points = [[], []]
        self.max_size_reg = 512
        self.tmat = np.identity(3)
        self.img_reg = None
        self.img_reg_bin = None
        self.mask = None
        self.apply_mask = True
        self.inv_reg = False
        self.results = {}
        self.dirname_res = [None, None]
        self.fixed_reg = False

        _, self.ax = plt.subplots(1, 4, figsize=(10, 4),
                                  gridspec_kw={'width_ratios': [1, 1, 1, 2]})

        if self.fnames_tot[0] is not None:
            self.fnames[0] = self.fnames_tot[0]
            self.load_files(0, self.fnames[0])
        if self.fnames_tot[1] is not None:
            self.fnames[1] = self.fnames_tot[1]
            self.load_files(1, self.fnames[1])

    def reinit(self):
        """ Reinitialize 'points', 'img_reg', 'img_reg_bin' and 'results' """
        self.points = [[], []]
        self.img_reg = None
        self.img_reg_bin = None
        self.results = {}
        self.mask = None

    def load_files(self, k, fnames):
        """ Load the k-th image files """
        if not isinstance(fnames, list):
            fnames = [fnames]

        self.fnames_tot[k] = fnames
        self.load_image(k, fnames[0])

    def load_image(self, k, fname):
        """ Load the k-th image """
        try:
            img = iio.imread(fname)
            success = True
        except Exception as _:
            self.terminal.write(f"Failed to load {fname}\n\n")
            success = False

        if success:
            self.reinit()
            self.imgs[k] = np.rot90(img, k=self.angles[k] / 90)
            self.dtypes[k] = img.dtype
            self.fnames[k] = fname
            self.binarization_k(k)
            self.update_rfactors_plotting()

    def get_shapes(self):
        """ Return the shapes related to the cropped (or not cropped) images """
        shapes = []
        for k in range(2):
            if self.rois[k] is not None:
                xmin, xmax, ymin, ymax = self.rois[k]
                shapes.append((ymax - ymin, xmax - xmin))
            else:
                shape = self.imgs[k].shape
                shapes.append([shape[0], shape[1]])
        return shapes

    def update_rfactors_plotting(self):
        """ Update the 'rfactors_plotting' wrt 'resolution' and 'imgs' sizes """
        if self.imgs[0] is None or self.imgs[1] is None:
            return

        imgs = [cropping(self.imgs[k], self.rois[k], verbosity=False) for k in range(2)]
        shapes = [imgs[0].shape[:2], imgs[1].shape[:2]]
        vmax = max(max(shapes[0]), max(shapes[1]))
        vmin = min(self.min_img_res, max(shapes[0]), max(shapes[1]))
        max_size = (vmax - vmin) * self.resolution + vmin
        self.rfactors_plotting = rescaling_factors(imgs, max_size)

    def binarization_k(self, k):
        """ Binarize the k-th image """
        if self.imgs[k] is None:
            return

        img = image_normalization(gray_conversion(self.imgs[k]))
        abs_threshold = absolute_threshold(cropping(img, self.rois[k]), self.thresholds[k])
        self.imgs_bin[k] = img > abs_threshold

        if self.bin_inversions[k]:
            self.imgs_bin[k] = ~self.imgs_bin[k]

    def binarization(self):
        """ Binarize the images """
        self.binarization_k(0)
        self.binarization_k(1)

    def crop_and_resize(self, imgs, verbosity=True):
        """ Crop and Resize the images"""
        imgs = [cropping(imgs[k], self.rois[k], verbosity=verbosity) for k in range(2)]
        if self.registration_model == 'StackReg':
            imgs = resizing(*imgs)
        return imgs

    def registration(self, registration_model=None, show_score=True):
        """ Calculate the transformation matrix 'tmat' and apply it """
        self.registration_calc(registration_model=registration_model)
        self.registration_apply(show_score=show_score)

    def registration_calc(self, registration_model=None):
        """ Calculate the transformation matrix 'tmat' """
        if registration_model in REG_MODELS:
            self.registration_model = registration_model

        if self.registration_model == 'StackReg':
            imgs_bin = self.crop_and_resize(self.imgs_bin)
            imgs_bin, rfacs = imgs_rescaling(imgs_bin, self.max_size_reg)
            self.tmat = STREG.register(*imgs_bin)
            self.tmat[:2, :2] *= rfacs[0] / rfacs[1]
            self.tmat[:2, 2] *= 1. / rfacs[1]

        elif self.registration_model == 'SIFT':
            imgs = self.crop_and_resize(self.imgs)
            imgs = [gray_conversion(img) for img in imgs]
            imgs, rfacs = imgs_rescaling(imgs, self.max_size_reg)
            self.tmat, self.points = sift(*imgs)
            self.tmat[:2, :2] *= rfacs[0] / rfacs[1]
            self.tmat[:2, 2] *= 1. / rfacs[1]
            self.points[0] = self.points[0] / rfacs[0]
            self.points[1] = self.points[1] / rfacs[1]

        elif self.registration_model == 'User-Driven':
            src = np.asarray(self.points[0])
            dst = np.asarray(self.points[1])
            self.tmat = estimate_transform('affine', src, dst).params

        elif self.registration_model == 'SIFT + StackReg':

            self.registration(registration_model='SIFT', show_score=False)

            # save/change input data
            tmat = self.tmat.copy()
            imgs = self.imgs.copy()
            imgs_bin = self.imgs_bin.copy()
            rois = self.rois.copy()

            # change temporarily input data
            self.imgs[1] = self.img_reg
            self.imgs[0] = cropping(self.imgs[0], self.rois[0]).astype(float)
            self.imgs[0][self.mask] = np.nan
            self.rois = [None, None]
            self.binarization()

            self.registration_calc(registration_model='StackReg')
            self.tmat = np.matmul(tmat, self.tmat)

            # re-set data to their original values
            self.registration_model = 'SIFT + StackReg'
            self.imgs = imgs
            self.imgs_bin = imgs_bin
            self.rois = rois

        else:
            raise IOError

        print()
        print(self.tmat)

    def registration_apply(self, show_score=True):
        """ Apply the transformation matrix 'tmat' to the moving image """
        if self.tmat is None:
            return

        imgs = self.crop_and_resize(self.imgs, verbosity=False)
        imgs_bin = self.crop_and_resize(self.imgs_bin, verbosity=False)

        k0, k1, tmat = 0, 1, self.tmat
        if self.inv_reg:  # inverse registr. from the fixed to the moving image
            k0, k1, tmat = 1, 0, np.linalg.inv(self.tmat)

        output_shape = imgs[k0].shape
        self.img_reg = warp(imgs[k1], tmat,
                            output_shape=output_shape,
                            preserve_range=True,
                            mode='constant', cval=np.nan, order=None)
        self.img_reg_bin = warp(imgs_bin[k1], tmat,
                                output_shape=output_shape[:2])

        self.mask = np.isnan(self.img_reg)
        if len(self.mask.shape) > 2:
            self.mask = self.mask.any(axis=-1)

        # score calculation and displaying
        if show_score:
            mismatch = np.logical_xor(imgs_bin[k0], self.img_reg_bin)
            mismatch[self.mask] = 0
            score = 100 * (1. - np.sum(mismatch) /
                           (mismatch.size - np.sum(self.mask)))

            msg = f"score : {score:.1f} % ({self.registration_model}"
            if "SIFT" in self.registration_model:
                msg += f" - nb_matches : {len(self.points[0])}"
            msg += ")"
            self.terminal.write(msg + "\n")

            self.results[self.registration_model] = {'score': score,
                                                     'tmat': self.tmat}

        return imgs[0], self.img_reg

    def set_dirname_res(self, dirname_res=None):
        """ Set dirname results 'dirname_res' """
        if dirname_res is None:
            initialdir = None
            if self.fnames_tot[1] is not None:
                initialdir = Path(self.fnames_tot[1][-1]).parent
            dirname_res = filedialog.askdirectory(initialdir=initialdir)
            if dirname_res is None:
                return

        dirname_res = Path(dirname_res)
        dirname_res.mkdir(exist_ok=True)

        self.dirname_res[0] = dirname_res / "fixed_images"
        self.dirname_res[1] = dirname_res / "moving_images"
        self.dirname_res[0].mkdir(exist_ok=True)
        self.dirname_res[1].mkdir(exist_ok=True)

    def apply_to_all(self, dirname_res=None):
        """ Apply the transformations to a set of images """
        if self.fnames_tot[0] is None:
            self.terminal.write("\nERROR: fixed images are not defined\n\n")
            return
        if self.fnames_tot[1] is None:
            self.terminal.write("\nERROR: moving images are not defined\n\n")
            return

        n0, n1 = len(self.fnames_tot[0]), len(self.fnames_tot[1])
        if not (n0 == 1 or n0 == n1):
            msg = f"\nERROR: fixed images should be 1 or {n1} files.\n"
            msg += f"{n0} has been given\n\n"
            self.terminal.write(msg)
            return

        self.terminal.write("\n")

        self.set_dirname_res(dirname_res=dirname_res)

        fnames_fixed = self.fnames_tot[0]
        fnames_moving = self.fnames_tot[1]
        for i, fname_moving in enumerate(fnames_moving):
            fname_fixed = fnames_fixed[0] if n0 == 1 else fnames_fixed[i]
            names = [Path(fname_fixed).name, Path(fname_moving).name]
            self.terminal.write(f"{i + 1}/{n1} {names[0]} - {names[1]}:\n")

            try:
                self.load_image(0, fname=fname_fixed)
                self.load_image(1, fname=fname_moving)
                if not self.fixed_reg:
                    self.registration_calc()
                imgs = self.registration_apply()
                for k in range(2):
                    iio.imwrite(self.dirname_res[k] / names[k],
                                imgs[k].astype(self.dtypes[k]))

            except:
                self.terminal.write("FAILED\n")

        self.terminal.write("\n")

    def save_params(self, fname_json=None):
        """ Save parameters in a .json file """
        if fname_json is None:
            fname_json = filedialog.asksaveasfilename(defaultextension='.json')
            if fname_json is None:
                return

        params = {}
        for key in KEYS:
            params.update({key: eval(f"self.{key}")})

        with open(fname_json, 'w', encoding='utf-8') as fid:
            json.dump(params, fid, ensure_ascii=False, indent=4)

    @staticmethod
    def reload_params(fname_json=None, obj=None):
        """ Reload parameters from a .json file and
            Return an ImagesAlign() object"""

        if fname_json is None:
            fname_json = filedialog.askopenfilename(defaultextension='.json')

        if not os.path.isfile(fname_json):
            raise IOError(f"{fname_json} is not a file")

        if obj is not None:
            assert isinstance(obj, ImagesAlign)

        with open(fname_json, 'r', encoding='utf-8') as fid:
            params = json.load(fid)

        imgalign = obj or ImagesAlign()
        for key, value in params.items():
            setattr(imgalign, key, value)
        return imgalign

    def plot_all(self, ax=None):
        """ Plot all the axis """
        if ax is not None:
            self.ax = ax

        for k in range(len(self.ax)):
            self.plot_k(k)

    def plot_k(self, k):
        """ Plot the k-th axis """
        self.ax[k].clear()

        if k in [0, 1]:
            self.plot_fixed_or_moving_image(k)

        elif k == 2:
            self.plot_juxtaposed_images()

        elif k == 3:
            self.plot_combined_images()

        else:
            raise IOError

        self.ax[k].autoscale(tight=True)

    def plot_fixed_or_moving_image(self, k):
        """ Plot the fixed or the moving image """

        if self.imgs[k] is None:
            return

        self.ax[k].set_title(['Fixed image', 'Moving image'][k])
        extent = [0, self.imgs[k].shape[1], 0, self.imgs[k].shape[0]]

        if self.binarized:
            img = np.zeros_like(self.imgs_bin[k], dtype=int)
            img[self.imgs_bin[k]] = 2 * k - 1
            img = rescaling(img, self.rfactors_plotting[k])
            self.ax[k].imshow(img, cmap=CMAP_BINARIZED, vmin=-1, vmax=1,
                              extent=extent)
        else:
            img = self.imgs[k].copy()
            img = rescaling(img, self.rfactors_plotting[k])
            self.ax[k].imshow(img, cmap='gray', extent=extent)

        if self.rois[k] is not None:
            xmin, xmax, ymin, ymax = self.rois[k]
            width, height = xmax - xmin, ymax - ymin
            self.ax[k].add_patch(Rectangle((xmin, ymin), width, height,
                                           ec='y', fc='none'))

    def plot_juxtaposed_images(self):
        """ Plot the juxtaposed images """

        self.ax[2].set_title("Juxtaposed images")

        imgs = [self.ax[k].get_images() for k in range(2)]
        if len(imgs[0]) == 0 or len(imgs[1]) == 0:
            return

        alignment = self.juxt_alignment
        rfacs = self.rfactors_plotting

        arrs = []
        for k in range(2):
            arr = imgs[k][0].get_array()
            if self.rois[k] is not None:
                roi = (np.asarray(self.rois[k]) * rfacs[k]).astype(int)
                arr = cropping(arr, roi, verbosity=False)
            arrs.append(arr)

        if not self.binarized:
            arrs = [image_normalization(arr) for arr in arrs]
        arrs = imgs_conversion(arrs)
        img, offset = concatenate_images(arrs[0], arrs[1], alignment=alignment)
        extent = [0, img.shape[1], 0, img.shape[0]]

        if self.binarized:
            self.ax[2].imshow(img, cmap=CMAP_BINARIZED, vmin=-1, vmax=1,
                              extent=extent)
        else:
            self.ax[2].imshow(img, cmap='gray', extent=extent)

        npoints = len(self.points[0])
        if npoints > 0:
            np.random.seed(0)
            random.seed(0)
            rng = np.random.default_rng(0)
            inds = random.sample(range(0, npoints), min(10, npoints))
            points = np.asarray(self.points)
            for src, dst in zip(points[0][inds], points[1][inds]):
                x0, y0 = src[0] * rfacs[0], arrs[0].shape[0] - src[1] * rfacs[0]
                x1, y1 = dst[0] * rfacs[1], arrs[1].shape[0] - dst[1] * rfacs[1]
                x = [x0, x1 + offset[0]]
                y = [y0, y1 + offset[1]]
                self.ax[2].plot(x, y, '-', color=rng.random(3))

    def plot_combined_images(self):
        """ Plot the combined images """

        self.ax[3].set_title("Combined images")

        if self.imgs[0] is None or self.imgs[1] is None:
            return

        rfacs = self.rfactors_plotting

        if self.binarized:
            imgs = self.crop_and_resize(self.imgs_bin, verbosity=False)
            if self.img_reg_bin is not None:
                k0, k1 = (0, 1) if self.inv_reg else (1, 0)
                imgs[k0] = self.img_reg_bin
            imgs = padding(*imgs)
            img = np.zeros_like(imgs[0], dtype=float)
            img[imgs[1] * ~imgs[0]] = 1
            img[imgs[0] * ~imgs[1]] = -1
            if self.apply_mask and self.mask is not None:
                img[self.mask] = np.nan
            k0, k1 = (0, 1) if self.inv_reg else (1, 0)
            img = rescaling(img, rfacs[k1])
            self.ax[3].imshow(img, cmap=CMAP_BINARIZED, vmin=-1, vmax=1)

        else:
            imgs = [cropping(self.imgs[k], self.rois[k], verbosity=False) for k in range(2)]
            imgs = [rescaling(imgs[k], rfacs[k]) for k in range(2)]
            if self.img_reg is not None:
                k0, k1 = (0, 1) if self.inv_reg else (1, 0)
                imgs[k0] = rescaling(self.img_reg, rfacs[k1])
            imgs = padding(*imgs)
            imgs = [image_normalization(img) for img in imgs]
            imgs = imgs_conversion(imgs)
            img = np.stack([imgs[0], imgs[1]], axis=0)
            if self.apply_mask:
                img = np.mean(img, axis=0)
            else:
                img = np.nanmean(img, axis=0)
            self.ax[3].imshow(img, cmap='gray')