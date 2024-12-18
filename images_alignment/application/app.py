"""
Tkinter-application for the images alignment
"""
import shutil
from tkinter import Tk, messagebox

from images_alignment import ImagesAlign, TMP_DIR
from images_alignment.application.view import View


class App:
    """
    Application for the images alignment

    Parameters
    ----------
    root: Tkinter.Tk object
        Root window
    size: str, optional
        Size of the root window
    force_terminal_exit: bool, optional
        Key to force terminal session to exit after 'root' destroying
    fnames_fixed, fnames_moving: iterables of str, optional
        Images pathnames related to fixed and moving images resp. to handle
    rois: list of 2 iterables, optional
        rois (regions of interest) attached to the fixed and moving images, each defining as:
         [xmin, xmax, ymin, ymax]
    thresholds: iterable of 2 floats, optional
        Thresholds used to binarize the images. Default values are [0.5, 0.5]
    bin_inversions: iterable of 2 bools, optional
        Activation keywords to reverse the image binarization
    """

    def __init__(self,
                 root, size="1450x860", force_terminal_exit=True,
                 fnames_fixed=None,
                 fnames_moving=None,
                 rois=None,
                 thresholds=None,
                 bin_inversions=None):
        root.title("images_alignment")
        root.geometry(size)
        self.root = root
        self.force_terminal_exit = force_terminal_exit
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.model = ImagesAlign(fnames_fixed=fnames_fixed,
                                 fnames_moving=fnames_moving,
                                 rois=rois,
                                 thresholds=thresholds,
                                 bin_inversions=bin_inversions)

        self.view = View(self.root, self.model)
        self.view.fselectors[0].add_items(fnames=fnames_fixed)
        self.view.fselectors[1].add_items(fnames=fnames_moving)

    def on_closing(self):
        """ To quit 'properly' the application """
        if messagebox.askokcancel("Quit", "Would you like to quit ?"):
            shutil.rmtree(TMP_DIR)
            self.root.destroy()


def launcher(fname_json=None):
    """ Launch the appli """

    root = Tk()
    appli = App(root)

    if fname_json is not None:
        appli.reload(fname_json=fname_json)

    root.mainloop()


if __name__ == '__main__':
    launcher()
