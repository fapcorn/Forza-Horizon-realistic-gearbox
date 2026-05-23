# 2024/8/28 v2.3.2 revised vgamepad version
# Original by GinoLin980

import sys; sys.dont_write_bytecode = True

import socket
import keyboard, time
import select
import vgamepad as vg

from DATAOUT import *
import GUI
from dyno import *

# splash
try:
    import pyi_splash  # type: ignore
    pyi_splash.close()
except ImportError:
    pass


class Gearbox():

    def __init__(self) -> None:

        self.VERSION: str = "v2.4-vgamepad"
        self.MS_STORE = True

        # Network
        self.UDP_IP: str = "127.0.0.1" if not self.MS_STORE else "0.0.0.0"
        self.UDP_PORT: int = 8000

        # Virtual controller
        self.gamepad = vg.VX360Gamepad()

        self.UPSHIFT_BUTTON = vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER
        self.DOWNSHIFT_BUTTON = vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER

        self.SHIFT_PRESS_TIME = 0.03

        # Store data
        self.RETURNED_DATA: dict

        # Car information
        self.gas: float = 0
        self.brake: float
        self.gear: int
        self.slip: bool

        # Decision information
        self.kickdown: bool = False
        self.jump_gears: float = 0
        self.sports_high_rpm: bool = False
        self.sports_high_rpm_time: float = time.time()

        self.last_shift_time: float = 0
        self.last_upshift_time: float = 0
        self.last_downshift_time: float = 0
        self.last_uphill_time: float = 0

        self.aggressiveness: float = 0
        self.last_inc_aggr_time: float = 0

        self.PREVENT: float = self.last_downshift_time + 1.2
        self.WAIT_TIME_BETWEEN_DOWNSHIFTS: float = 0.7

        # Misc
        self.run_dyno = False

        # Modes
        self.MODES: dict[str, list[float]] = {
            "Normal": [0.95, 0.35, 12, 0.12],
            "Sports": [0.8, 0.4, 24, 0.25],
            "Eco": [1, 0.35, 6, 0.12],
            "Manual": [0, 0, 0, 0]
        }

        self.gas_thresholds: list[float] = self.MODES["Normal"]
        self.current_drive_mode: str = "D"

        # GUI info
        self.condition: dict[str, bool | float | str | int] = {
            "stop": False,
            "UDP_started": False,
            "gas": 0,
            "brake": 0,
            "drive_mode": "D",
            "gear": 0,
            "run_dyno": False,
            "dyno_data": {
                "rpm": 0,
                "hp": 0,
                "torque": 0
            }
        }

        self.APP = GUI.FHRG_GUI(
            condition=self.condition,
            VERSION=self.VERSION
        )

    def analyzeInput(self) -> None:

        self.gas = self.RETURNED_DATA["Accel"] / 255
        self.brake = self.RETURNED_DATA["Brake"] / 255
        self.gear = self.RETURNED_DATA["Gear"]

        idleRPM: float = self.RETURNED_DATA["EngineIdleRpm"]

        # max rpm
        if self.RETURNED_DATA["EngineMaxRpm"] <= 5000:

            if self.current_drive_mode == "S":
                self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.65
            else:
                self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.6

        else:
            self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.86

        self.rpm_range_size = (
            self.RETURNED_DATA["EngineMaxRpm"] - idleRPM
        ) / (
            3 if self.max_shift_rpm > 4000 and self.max_shift_rpm < 8000 else 5
        )

        new_aggr = min(
            1,
            max(
                (self.gas - self.gas_thresholds[1]) /
                (self.gas_thresholds[0] - self.gas_thresholds[1]) * 1.5,

                (self.brake - (self.gas_thresholds[1] - 0.3)) /
                (self.gas_thresholds[0] - self.gas_thresholds[1]) * 1.6
            )
        )

        if new_aggr > self.aggressiveness and self.gear > 0:

            self.aggressiveness = new_aggr
            self.last_inc_aggr_time = time.time()

        if time.time() > self.last_inc_aggr_time + 4:
            self.aggressiveness -= 1 / self.gas_thresholds[2]

        self.aggressiveness = max(
            self.aggressiveness,
            self.gas_thresholds[3]
        )

        self.rpm_range_top = (
            idleRPM + 100 +
            ((self.max_shift_rpm - idleRPM - 400) * self.aggressiveness)
        )

        self.rpm_range_bottom = max(
            idleRPM + (min(self.gear, 6) * 70) * 0.96,
            self.rpm_range_top - self.rpm_range_size
        )

        if self.rpm_range_top <= 6200:
            self.rpm_range_bottom -= 400

        # Slip detection
        if (
            self.RETURNED_DATA["TireSlipRatioFrontLeft"] > 1
            or self.RETURNED_DATA["TireSlipRatioFrontRight"] > 1
            or self.RETURNED_DATA["TireSlipRatioRearLeft"] > 1
            or self.RETURNED_DATA["TireSlipRatioRearRight"] > 1
        ):
            self.slip = True
        else:
            self.slip = False

        # Sports high RPM hold
        if self.current_drive_mode == "S" and self.gas > 0.6:
            self.sports_high_rpm = True
            self.sports_high_rpm_time = time.time()

        if time.time() - self.sports_high_rpm_time > 5:
            self.sports_high_rpm = False

        # kickdown reset
        if time.time() > self.last_downshift_time + 2:
            self.kickdown = False

    def makeDecision(self) -> None:

        speed: float = self.RETURNED_DATA["Speed"]
        rpm: float = self.RETURNED_DATA["CurrentEngineRpm"]

        if self.kickdown is False:

            if self.brake > 0:
                self.WAIT_TIME_BETWEEN_DOWNSHIFTS = 0.2
            else:
                self.WAIT_TIME_BETWEEN_DOWNSHIFTS = 0.7

        # uphill logic
        if self.RETURNED_DATA["Pitch"] < -0.12:

            self.gas_thresholds = self.MODES["Sports"]
            self.last_uphill_time = time.time()

        elif self.last_uphill_time - time.time() < 5:

            match self.current_drive_mode:

                case "D":
                    self.gas_thresholds = self.MODES["Normal"]

                case "E":
                    self.gas_thresholds = self.MODES["Eco"]

        # no shifting conditions
        if (
            time.time() < self.last_shift_time + 0.2
            or self.gear < 1
            or time.time() < self.last_upshift_time + 1.3
            or time.time() < self.last_downshift_time + self.WAIT_TIME_BETWEEN_DOWNSHIFTS
        ):
            return

        # UPSHIFT
        if (
            rpm > self.rpm_range_top
            and not self.slip
            and time.time() > self.PREVENT
            and self.brake == 0
            and self.gas > 0
            and speed > 4
            and (time.time() - self.last_uphill_time > 5 or rpm > self.max_shift_rpm)
        ):

            if (
                rpm > self.rpm_range_size * 2
                and rpm < self.max_shift_rpm - 500
                and self.sports_high_rpm
            ):

                if self.current_drive_mode == "S":
                    return

            self.shiftUp()
            self.PREVENT = self.last_downshift_time + 1.2

        # DOWNSHIFT
        elif (
            rpm < self.rpm_range_bottom
            and not self.slip
            and self.gear > 1
            and time.time() > self.last_downshift_time + self.WAIT_TIME_BETWEEN_DOWNSHIFTS
            and not rpm > self.rpm_range_size * 2.3
            and not (self.gas < 0.35 and rpm < 1800)
            and (
                self.gear > 2
                or (
                    (self.gear == 2 and (self.aggressiveness >= 0.95 or speed <= 4))
                    or (self.gear >= 4 and self.brake > 0)
                )
            )
        ):

            # kickdown
            if (
                self.kickdown is False
                and self.gas > 0.7
                and rpm < self.rpm_range_size * 2
                and self.gear > 4
            ):

                self.kickdown = True
                self.PREVENT = 0

                jump_gears = int((self.rpm_range_size * 3.8) // rpm)

                for _ in range(jump_gears):

                    if self.gear > 2:

                        self.shiftDown()
                        time.sleep(0.03)
                        self.gear -= 1

                    else:
                        break

            if self.kickdown:
                return

            self.shiftDown()

    def press_virtual_button(self, button) -> None:

        self.gamepad.press_button(button=button)
        self.gamepad.update()

        time.sleep(self.SHIFT_PRESS_TIME)

        self.gamepad.release_button(button=button)
        self.gamepad.update()

    def shiftUp(self) -> None:

        print("UPSHIFT")

        try:

            self.press_virtual_button(self.UPSHIFT_BUTTON)

            self.last_shift_time = time.time()
            self.last_upshift_time = time.time()

        except Exception as e:
            print(f"Upshift error: {e}")

    def shiftDown(self) -> None:

        print("DOWNSHIFT")

        try:

            self.press_virtual_button(self.DOWNSHIFT_BUTTON)

            self.last_shift_time = time.time()
            self.last_downshift_time = time.time()

        except Exception as e:
            print(f"Downshift error: {e}")

    # drive mode hotkeys
    def mode_changer(self) -> None:

        if keyboard.is_pressed("7"):

            self.current_drive_mode = "D"
            self.gas_thresholds = self.MODES["Normal"]

        elif keyboard.is_pressed("8"):

            self.current_drive_mode = "S"
            self.gas_thresholds = self.MODES["Sports"]

        elif keyboard.is_pressed("9"):

            self.current_drive_mode = "E"
            self.gas_thresholds = self.MODES["Eco"]

        elif keyboard.is_pressed("0"):

            self.current_drive_mode = "M"
            self.gas_thresholds = self.MODES["Manual"]

    def dyno_func(self):

        if keyboard.is_pressed("F1"):

            self.APP.dyno_page.show()
            self.run_dyno = True

        elif keyboard.is_pressed("F2"):

            self.APP.dyno.clear_chart()

        if self.run_dyno is True:

            if self.RETURNED_DATA["EngineMaxRpm"] <= 5000:

                if self.current_drive_mode == "S":
                    self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.65
                else:
                    self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.6

            else:
                self.max_shift_rpm = self.RETURNED_DATA["EngineMaxRpm"] * 0.86

            if self.RETURNED_DATA["IsRaceOn"] != 0:

                if self.condition["gas"] > 0.8:

                    self.APP.dyno.add_new_power_data({
                        "rpm": int(self.RETURNED_DATA["CurrentEngineRpm"]),
                        "hp": self.RETURNED_DATA["Power"] / 746,
                        "torque": self.RETURNED_DATA["Torque"]
                    })

                    if self.RETURNED_DATA["CurrentEngineRpm"] > self.max_shift_rpm:

                        self.APP.dyno.after_plot()
                        self.run_dyno = False

    def main(self) -> None:

        # wait for UDP
        while True:

            if self.condition["stop"]:
                sys.exit()

            self.APP.update()

            if UDPconnectable(self.UDP_IP, self.UDP_PORT):
                break

        # UDP server
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.UDP_IP, self.UDP_PORT))

        data, _addr = sock.recvfrom(1500)

        self.RETURNED_DATA = get_data(data)

        self.condition["UDP_started"] = True
        self.condition["gas"] = 0
        self.condition["brake"] = 0
        self.condition["gear"] = 0
        self.condition["drive_mode"] = "D"

        self.APP.UDP_started(self.condition)

        self.APP.update_idletasks()
        self.APP.update()

        # main loop
        while True:

            self.mode_changer()

            ready = select.select([sock], [], [], 3)

            if ready[0]:
                pass
            else:
                self.APP.quit()
                self.APP.destroy()
                sys.exit()

            data, _addr = sock.recvfrom(1500)

            self.RETURNED_DATA = get_data(data)

            self.condition["gas"] = self.RETURNED_DATA["Accel"] / 255
            self.condition["brake"] = self.RETURNED_DATA["Brake"] / 255
            self.condition["gear"] = self.RETURNED_DATA["Gear"]
            self.condition["drive_mode"] = self.current_drive_mode

            self.APP.update_home(self.condition)

            if self.condition["stop"]:
                sys.exit()

            self.APP.update()
            self.APP.update_idletasks()

            self.dyno_func()

            if self.run_dyno:
                continue

            # skip if not driving
            if self.RETURNED_DATA["IsRaceOn"] == 0:
                continue

            # skip manual mode
            if self.current_drive_mode != "M":

                self.analyzeInput()
                self.makeDecision()

            # reduce polling jitter
            time.sleep(0.005)


if __name__ == "__main__":

    FH_gearbox = Gearbox()
    FH_gearbox.main()

    sys.exit()
