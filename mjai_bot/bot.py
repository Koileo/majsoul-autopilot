import json
from dataclasses import dataclass
from mjai import Bot
from mjai.mlibriichi.state import PlayerState  # type: ignore
from .logger import logger

class MjaiStateTracker(Bot):
    """Track MJAI game state so the runner can inspect seat/hand details."""
    def __init__(self):
        super().__init__()

    def think(self) -> str:
        """
        tsumogiri
        """
        if self.can_discard:
            tile_str = self.last_self_tsumo
            return self.action_discard(tile_str)
        else:
            return self.action_nothing()

    def react(self, input_str: str = None, input_list: list[dict] = None) -> str:
        try:
            if input_str:
                events = json.loads(input_str)
            elif input_list:
                events = input_list
            else:
                raise ValueError("Empty input")
            if len(events) == 0:
                raise ValueError("Empty events")
            for event in events:
                if event["type"] == "start_game":
                    self.player_id = event["id"]
                    self.player_state = PlayerState(self.player_id)
                    self.__discard_events = []
                    self.__call_events = []
                    self.__dora_indicators = []
                if event["type"] == "start_kyoku" or event["type"] == "dora":
                    self.__dora_indicators.append(event["dora_marker"])
                if event["type"] == "dahai":
                    self.__discard_events.append(event)
                if event["type"] in [
                    "chi",
                    "pon",
                    "daiminkan",
                    "kakan",
                    "ankan",
                ]:
                    self.__call_events.append(event)
                logger.debug(f"Event: {event}")
                self.action_candidate = self.player_state.update(
                    json.dumps(event)
                )

            # NOTE: Skip `think()` if the player's riichi is accepted and
            # no call actions are allowed.
            if (
                self.self_riichi_accepted
                and not (self.can_agari or self.can_kakan or self.can_ankan)
                and self.can_discard
            ):
                return self.action_discard(self.last_self_tsumo)

            resp = self.think()
            return resp

        except Exception as e:
            logger.error(f"Exception: {str(e)}")
            logger.error("Brief info:")
            logger.error(self.brief_info())

        return json.dumps({"type": "none"}, separators=(",", ":"))
    
    # ============================================= #
    #                Custom Methods                 #
    # ============================================= #
    @dataclass
    class ChiCandidates:
        chi_low_meld: tuple[str, tuple[str, str]] = None
        chi_mid_meld: tuple[str, tuple[str, str]] = None
        chi_high_meld: tuple[str, tuple[str, str]] = None

    def find_chi_candidates_simple(self) -> ChiCandidates:
        """

        Examples:
            >>> bot.find_chi_candidates_simple()
        """
        chi_candidates: MjaiStateTracker.ChiCandidates = MjaiStateTracker.ChiCandidates()

        color = self.last_kawa_tile[1]
        chi_num = int(self.last_kawa_tile[0])
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}r" in self.tehai_mjai
            and f"{chi_num-1}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-2}{color}r", f"{chi_num-1}{color}")
            chi_candidates.chi_high_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}" in self.tehai_mjai
            and f"{chi_num-1}{color}r" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-2}{color}", f"{chi_num-1}{color}r")
            chi_candidates.chi_high_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}" in self.tehai_mjai
            and f"{chi_num-1}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-2}{color}", f"{chi_num-1}{color}")
            chi_candidates.chi_high_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}r" in self.tehai_mjai
            and f"{chi_num+1}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-1}{color}r", f"{chi_num+1}{color}")
            chi_candidates.chi_mid_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}" in self.tehai_mjai
            and f"{chi_num+1}{color}r" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-1}{color}", f"{chi_num+1}{color}r")
            chi_candidates.chi_mid_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}" in self.tehai_mjai
            and f"{chi_num+1}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num-1}{color}", f"{chi_num+1}{color}")
            chi_candidates.chi_mid_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}r" in self.tehai_mjai
            and f"{chi_num+2}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num+1}{color}r", f"{chi_num+2}{color}")
            chi_candidates.chi_low_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}" in self.tehai_mjai
            and f"{chi_num+2}{color}r" in self.tehai_mjai
        ):
            consumed = (f"{chi_num+1}{color}", f"{chi_num+2}{color}r")
            chi_candidates.chi_low_meld = (
                self.last_kawa_tile,
                consumed,
            )
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}" in self.tehai_mjai
            and f"{chi_num+2}{color}" in self.tehai_mjai
        ):
            consumed = (f"{chi_num+1}{color}", f"{chi_num+2}{color}")
            chi_candidates.chi_low_meld = (
                self.last_kawa_tile,
                consumed,
            )

        return chi_candidates

    
    def find_chi_consume_simple(self) -> list[list[str]]:
        """

        Examples:
            >>> bot.find_chi_consume_simple()

        """
        chi_candidates = []

        color = self.last_kawa_tile[1]
        chi_num = int(self.last_kawa_tile[0])
        tehai_mjai = self.tehai_mjai
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}r" in tehai_mjai
            and f"{chi_num-1}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num-2}{color}r", f"{chi_num-1}{color}"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}" in tehai_mjai
            and f"{chi_num-1}{color}r" in tehai_mjai
        ):
            consumed = [f"{chi_num-2}{color}", f"{chi_num-1}{color}r"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_high
            and f"{chi_num-2}{color}" in tehai_mjai
            and f"{chi_num-1}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num-2}{color}", f"{chi_num-1}{color}"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}r" in tehai_mjai
            and f"{chi_num+1}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num-1}{color}r", f"{chi_num+1}{color}"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}" in tehai_mjai
            and f"{chi_num+1}{color}r" in tehai_mjai
        ):
            consumed = [f"{chi_num-1}{color}", f"{chi_num+1}{color}r"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_mid
            and f"{chi_num-1}{color}" in tehai_mjai
            and f"{chi_num+1}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num-1}{color}", f"{chi_num+1}{color}"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}r" in tehai_mjai
            and f"{chi_num+2}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num+1}{color}r", f"{chi_num+2}{color}"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}" in tehai_mjai
            and f"{chi_num+2}{color}r" in tehai_mjai
        ):
            consumed = [f"{chi_num+1}{color}", f"{chi_num+2}{color}r"]
            chi_candidates.append(consumed)
        if (
            self.can_chi_low
            and f"{chi_num+1}{color}" in tehai_mjai
            and f"{chi_num+2}{color}" in tehai_mjai
        ):
            consumed = [f"{chi_num+1}{color}", f"{chi_num+2}{color}"]
            chi_candidates.append(consumed)

        return chi_candidates

    def find_pon_consume_simple(self) -> list[list[str]]:
        """
        Example:
            >>> bot.find_pon_consume_simple()
            [
                ["5m", "5m"],
                ["5mr", "5m"],
            ]
        """
        pon_candidates = []
        if self.last_kawa_tile[0] == "5" and self.last_kawa_tile[1] != "z":
            if self.tehai_mjai.count(self.last_kawa_tile[:2]) >= 2:
                consumed = [self.last_kawa_tile[:2], self.last_kawa_tile[:2]]
                pon_candidates.append(consumed)
            if (
                self.tehai_mjai.count(self.last_kawa_tile[:2]) >= 1 and 
                self.tehai_mjai.count(self.last_kawa_tile[:2] + "r") == 1
            ):
                consumed = [
                    self.last_kawa_tile[:2] + "r",
                    self.last_kawa_tile[:2],
                ]
                pon_candidates.append(consumed)
            return pon_candidates
        else:
            consumed = [
                self.last_kawa_tile,
                self.last_kawa_tile,
            ]
            pon_candidates.append(consumed)
        return pon_candidates
