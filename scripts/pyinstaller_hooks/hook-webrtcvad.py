# The contrib hook copies metadata of "webrtcvad"; we install the "webrtcvad-wheels" build.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
