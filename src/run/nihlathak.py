from char.i_char import IChar
from config import Config
from logger import Logger
from pather import Location, Pather
from item.pickit import PickIt
import template_finder
from town.town_manager import TownManager
from utils.custom_mouse import mouse
from ui_manager import center_mouse
from utils.misc import cut_roi, wait
from dataclasses import dataclass
from screen import convert_abs_to_monitor, convert_abs_to_screen, convert_screen_to_abs, convert_screen_to_monitor, grab
from automap_finder import toggle_automap
import random, keyboard, time, cv2, math
import numpy as np

from ui import loading, waypoint

pillar_offsets = [
    (-22, 16), #bottm
    (97, 15), #right
    (98, -69), #top
    (-23, -69), #left
]

nih_loc_name = ["bottom", "right", "top", "left"]

class Nihlathak:

    name = "run_nihlathak"

    def __init__(
        self,
        pather: Pather,
        town_manager: TownManager,
        char: IChar,
        pickit: PickIt,
        runs: list[str]
    ):
        self._pather = pather
        self._town_manager = town_manager
        self._char = char
        self._pickit = pickit
        self._runs = runs
        self._roi = Config().ui_roi["cut_skill_bar"]
        self._head_pos = convert_abs_to_monitor((0, -80))

    def approach(self, start_loc: Location) -> bool | Location:
        Logger.info("Run Nihlathak")
        if not self._char.capabilities.can_teleport_natively:
            raise ValueError("Nihlathak requires teleport")
        if not self._town_manager.open_wp(start_loc):
            return False
        if waypoint.use_wp("Halls of Pain", curr_active_act = self._town_manager.get_act_idx_from_location(start_loc)): # use Halls of Pain Waypoint (5th in A5)
            return Location.A5_NIHLATHAK_START
        return False

    def get_pillar_on_automap(self, automap_on=False):
        ref = grab()
        toggle_automap()
        if automap_on:
            img = ref.copy()
            match = template_finder.search(["NI2_STAIRS_AUTOMAP"], img, roi=self._roi, threshold=0.80, use_grayscale=False)
            while template_finder.search(["NI2_STAIRS_AUTOMAP"], inp_img=(ref:=grab(True)), roi=self._roi, threshold=0.80, use_grayscale=False).valid:
                time.sleep(0.02)
        else:
            match = template_finder.search_and_wait(["NI2_STAIRS_AUTOMAP"], roi=self._roi, threshold=0.80, use_grayscale=False, suppress_debug=True, timeout=1)
            img = grab().copy()
        if match.valid:
            center = match.center
        else:
            # try small template
            if not (match := template_finder.search(["NI2_STAIRS_SMALL_AUTOMAP"], img, roi=[540,260,200,200], threshold=0.92, use_grayscale=False, best_match=True)).valid:
                cv2.imwrite(f"./debug_screenshots/nihlathak_failed_{time.strftime('%m%d_%H%M%S')}.png", cut_roi(img, self._roi))
                return -1
            center = (match.center[0] + 2, match.center[1] + 4)

        diff_blue = np.int16(img[:,:,0]) - np.int16(ref[:,:,0])
        min_sum = 1000000
        empty_pillar = -1
        for i, (x,y) in enumerate(pillar_offsets):
            x += center[0]
            y += center[1]
            diff = np.clip(diff_blue[y-4:y+4,x-2:x+2].ravel(), 0, 255)
            sum = np.sum(diff)
            if sum < min_sum:
                min_sum = sum
                empty_pillar = i
        '''
        for i, (x,y) in enumerate(pillar_offsets):
            x += center[0]
            y += center[1]
            cv2.rectangle(img, (x-3, y-5), (x+3, y+5), (0,0,255) if empty_pillar != i else (0,255,0), 1)
        img = cv2.addWeighted(img, 0.5, original, 0.5, 0)
        cv2.imwrite(f"./debug_screenshots/nihlathak_pillar_{time.strftime('%m%d_%H%M%S')}.png", cut_roi(img, self._roi))
        '''
        return empty_pillar

    def battle(self, do_pre_buff: bool) -> bool | tuple[Location, bool]:
        # TODO: We might need a second template for each option as merc might run into the template and we dont find it then
        # Let's check which layout ("NI1_A = bottom exit" , "NI1_B = large room", "NI1_C = small room")
        template_match = template_finder.search_and_wait(["NI1_A", "NI1_B", "NI1_C"], threshold=0.65, roi=self._roi, timeout=20)
        if not template_match.valid:
            return False
        if do_pre_buff:
            self._char.pre_buff()

        # Depending on what template is found we do static pathing to the stairs on level1.
        # Its xpects that the static routes defined in game.ini are named: "ni1_a", "ni1_b", "ni1_c"
        self._pather.traverse_nodes_fixed(template_match.name.lower(), self._char)
        found_loading_screen_func = lambda: loading.wait_for_loading_screen(2.0) or \
            template_finder.search_and_wait(["NI2_SEARCH_0", "NI2_SEARCH_1"], threshold=0.8, roi=self._roi, timeout=0.5).valid
        # look for stairs
        try_cnt = 0
        while not self._char.select_by_template(["NI1_STAIRS", "NI1_STAIRS_2", "NI1_STAIRS_3", "NI1_STAIRS_4"], found_loading_screen_func, threshold=0.63, timeout=1):
            # tele to left and try again
            if try_cnt > 2: return False
            pos_m = convert_abs_to_monitor((-100, 0))
            self._char.move(pos_m, force_move=True)
            mouse.move(*self._head_pos, is_async=True)
            try_cnt += 1
        # Wait until templates in lvl 2 entrance are found
        if not template_finder.search_and_wait(["NI2_SEARCH_0", "NI2_SEARCH_1", "NI2_SEARCH_2"], threshold=0.8, roi=self._roi, timeout=10).valid:
            return False
        #wait(1.0) # wait to make sure the red writing is gone once we check for the eye
        @dataclass
        class EyeCheckData:
            destination_static_path_key: str
            dest_static_path: list[tuple[int, int]]
            circle_static_path_key: str
            safe_dist_nodes: list[int]
            end_nodes: list[int]

        check_arr = [
            EyeCheckData("ni2_a_safe_dist", [(84, 710)] * 3, "ni2_circle_a", [500], [501]),
            EyeCheckData("ni2_b_safe_dist", [(1267, 595)] * 4, "ni2_circle_b", [505], [506]),
            EyeCheckData("ni2_c_safe_dist", [(1267, 13)] * 4, "ni2_circle_c", [510], [511]),
            EyeCheckData("ni2_d_safe_dist", [(65, 10)] * 3, "ni2_circle_d", [515], [516, 517]),
        ]

        self._pather.traverse_nodes_fixed([(1270,10), (270,148), (120,670)], self._char)
        if (nih_loc := self.get_pillar_on_automap(False)) < 0:
            Logger.error("Could not find pillars on automap!")
            toggle_automap(False)
            return (Location.A5_NIHLATHAK_END, False)
        Logger.info(f"Found Nihlathak at {nih_loc_name[nih_loc]}")
        self._pather.traverse_nodes_fixed(check_arr[nih_loc].dest_static_path[:1], self._char)
        #self._pather.traverse_nodes_automap([1520+nih_loc], self._char, threshold=0.90, do_pre_move=False, toggle_map=False)
        # Double check empty pillar
        nih_loc_1 = self.get_pillar_on_automap(True)
        path_to_safe_dist = check_arr[nih_loc].dest_static_path
        if nih_loc_1 >= 0 and nih_loc_1 != nih_loc:
            Logger.info(f"Nihlathak at {nih_loc_name[nih_loc_1]}? Check again...")
            # move one more step try again
            self._pather.traverse_nodes_fixed(check_arr[nih_loc].dest_static_path[0:1], self._char)
            nih_loc_2 = self.get_pillar_on_automap(False)
            if nih_loc_1 == nih_loc_2:
                nih_loc = nih_loc_1
                Logger.warning(f"Correct Nihlathak position to {nih_loc_name[nih_loc]}")
                self._pather.traverse_nodes_automap([2520+nih_loc], self._char, threshold=0.90, do_pre_move=False, toggle_map=False)
                path_to_safe_dist = check_arr[nih_loc].dest_static_path
            else:
                path_to_safe_dist = check_arr[nih_loc].dest_static_path[1:]
            toggle_automap(False)
        self._pather.traverse_nodes_fixed(path_to_safe_dist, self._char)
        self._pather.traverse_nodes_automap(check_arr[nih_loc].safe_dist_nodes, self._char, threshold=0.90, do_pre_move=False, force_tp=True)

        # Attack & Pick items
        if not self._char.kill_nihlathak(check_arr[nih_loc].end_nodes):
            return False
        wait(0.2, 0.3)
        picked_up_items = self._pickit.pick_up_items(self._char)
        return (Location.A5_NIHLATHAK_END, picked_up_items)
