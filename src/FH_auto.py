# 2024/5/9 v2.3 updated with custom tkinter
# By GinoLin980
# Revised vgamepad version

import sys; sys.dont_write_bytecode = True

import socket
import keyboard, time
import select
import vgamepad as vg

from DATAOUT import *
import GUI

# splash
try:
    import pyi_splash
    pyi_splash.close()
except ImportError:
    pass

# version
VERSION = "v2.3-vgamepad"

# network
UDP_IP = "127.0.0.1"
UDP_PORT = 8000

# virtual controller
gamepad = vg.VX360Gamepad()

UPSHIFT_BUTTON = vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER
DOWNSHIFT_BUTTON = vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER

SHIFT_PRESS_TIME = 0.03

# state
gas = 0
brake = 0
gear = 0
rpm = 0
speed = 0
slip = False

wait_time_between_downshifts = 0.8
last_shift_time = 0
last_upshift_time = 0
last_downshift_time = 0

kickdown = False
jump_gears = 0

sports_high_rpm = False
sports_high_rpm_time = 0

PREVENT = last_downshift_time + 1.2

aggressiveness = 0
last_inc_aggr_time = 0

# modes
MODES = {
    "Normal": [0.95, 0.35, 12, 0.12],
    "Sports": [0.8, 0.4, 24, 0.35],
    "Eco": [1, 0.35, 6, 0.12],
    "Manual": [0, 0, 0, 0]
}

gas_thresholds = MODES["Normal"]
current_drive_mode = "D"

# GUI state
condition = {
    "stop": False,
    "UDP_started": False,
    "gas": 0,
    "brake": 0,
    "drive_mode": "D",
    "gear": 0
}


def analyzeInput():

    global gas
    global brake
    global gear
    global slip
    global idleRPM
    global kickdown
    global max_shift_rpm
    global rpm_range_size
    global rpm_range_top
    global rpm_range_bottom
    global aggressiveness
    global last_inc_aggr_time
    global sports_high_rpm
    global sports_high_rpm_time

    gas = rt["Accel"] / 255
    brake = rt["Brake"] / 255
    gear = rt["Gear"]

    idleRPM = rt["EngineIdleRpm"]

    rpm_range_size = (
        rt["EngineMaxRpm"] - rt["EngineIdleRpm"]
    ) / 3

    rpm_range_top = rt["EngineMaxRpm"]

    max_shift_rpm = (
        rt["EngineMaxRpm"] * 0.86
        if rt["EngineMaxRpm"] < 4000
        else rt["EngineMaxRpm"] * 0.7
    )

    new_aggr = min(
        1,
        max(
            (gas - gas_thresholds[1]) /
            (gas_thresholds[0] - gas_thresholds[1]) * 1.5,

            (brake - (gas_thresholds[1] - 0.3)) /
            (gas_thresholds[0] - gas_thresholds[1]) * 1.6
        )
    )

    if new_aggr > aggressiveness and gear > 0:
        aggressiveness = new_aggr
        last_inc_aggr_time = time.time()

    if time.time() > last_inc_aggr_time + 4:
        aggressiveness -= 1 / gas_thresholds[2]

    aggressiveness = max(
        aggressiveness,
        gas_thresholds[3]
    )

    rpm_range_top = (
        idleRPM + 950 +
        ((max_shift_rpm - idleRPM - 300) * aggressiveness * 0.9)
    )

    rpm_range_bottom = max(
        idleRPM + (min(gear, 6) * 70),
        rpm_range_top - rpm_range_size
    )

    if (
           rt["TireSlipRatioFrontLeft"] > 1
        or rt["TireSlipRatioFrontRight"] > 1
        or rt["TireSlipRatioRearLeft"] > 1
        or rt["TireSlipRatioRearRight"] > 1
    ):
        slip = True
    else:
        slip = False

    if current_drive_mode == "S" and gas > 0.6:
        sports_high_rpm = True
        sports_high_rpm_time = time.time()

    if time.time() - sports_high_rpm_time > 5:
        sports_high_rpm = False

    if time.time() > last_downshift_time + 2:
        kickdown = False


def makeDecision():

    global gear
    global rpm
    global speed
    global kickdown
    global jump_gears
    global PREVENT
    global last_downshift_time
    global wait_time_between_downshifts

    speed = rt["Speed"]
    rpm = rt["CurrentEngineRpm"]

    if kickdown is False:

        if brake > 0:
            wait_time_between_downshifts = 0.2
        else:
            wait_time_between_downshifts = 0.7

    if (
        time.time() < last_shift_time + 0.2
        or gear < 1
        or time.time() < last_upshift_time + 1.3
        or time.time() < last_downshift_time + wait_time_between_downshifts
    ):
        return

    # UPSHIFT
    if (
        rpm > rpm_range_top
        and not slip
        and time.time() > PREVENT
        and brake == 0
        and gas > 0
        and speed > 4
    ):

        if (
            rpm > rpm_range_size * 2
            and rpm < max_shift_rpm - 500
            and sports_high_rpm
        ):

            if current_drive_mode == "S":
                return

        shiftUp()
        PREVENT = last_downshift_time + 1.2

    # DOWNSHIFT
    elif (
        rpm < rpm_range_bottom
        and not slip
        and gear > 1
        and time.time() > last_downshift_time + wait_time_between_downshifts
        and not rpm > rpm_range_size * 2.3
        and (
            gear > 2
            or (
                (gear == 2 and (aggressiveness >= 0.95 or speed <= 4))
                or (gear >= 4 and brake > 0)
            )
        )
    ):

        if (
            kickdown is False
            and gas > 0.7
            and rpm < rpm_range_size * 2
            and gear > 4
        ):

            kickdown = True
            PREVENT = 0

            jump_gears = int((rpm_range_size * 3.8) // rpm)

            for _ in range(jump_gears):

                if gear > 2:
                    shiftDown()
                    time.sleep(0.03)
                    gear -= 1
                else:
                    break

        if kickdown:
            return

        shiftDown()


def press_virtual_button(button):

    gamepad.press_button(button=button)
    gamepad.update()

    time.sleep(SHIFT_PRESS_TIME)

    gamepad.release_button(button=button)
    gamepad.update()


def shiftUp():

    global last_shift_time
    global last_upshift_time

    print("UPSHIFT")

    try:

        press_virtual_button(UPSHIFT_BUTTON)

        last_shift_time = time.time()
        last_upshift_time = time.time()

    except Exception as e:
        print(f"Upshift error: {e}")


def shiftDown():

    global last_shift_time
    global last_downshift_time

    print("DOWNSHIFT")

    try:

        press_virtual_button(DOWNSHIFT_BUTTON)

        last_shift_time = time.time()
        last_downshift_time = time.time()

    except Exception as e:
        print(f"Downshift error: {e}")


def mode_changer():

    global gas_thresholds
    global current_drive_mode

    if keyboard.is_pressed("7"):

        current_drive_mode = "D"
        gas_thresholds = MODES["Normal"]

    elif keyboard.is_pressed("8"):

        current_drive_mode = "S"
        gas_thresholds = MODES["Sports"]

    elif keyboard.is_pressed("9"):

        current_drive_mode = "E"
        gas_thresholds = MODES["Eco"]

    elif keyboard.is_pressed("0"):

        current_drive_mode = "M"
        gas_thresholds = MODES["Manual"]


def main():

    global rt
    global addr

    while True:

        if condition["stop"]:
            sys.exit()

        APP.update()

        if UDPconnectable(UDP_IP, UDP_PORT):
            break

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    data, addr = sock.recvfrom(1500)

    rt = get_data(data)

    condition["UDP_started"] = True
    condition["gas"] = rt["Accel"] / 255
    condition["brake"] = rt["Brake"] / 255
    condition["gear"] = rt["Gear"]
    condition["drive_mode"] = current_drive_mode

    APP.UDP_started(condition)

    APP.update_idletasks()
    APP.update()

    while True:

        mode_changer()

        ready = select.select([sock], [], [], 3)

        if ready[0]:
            pass
        else:
            APP.quit()
            APP.destroy()
            sys.exit()

        data, addr = sock.recvfrom(1500)

        rt = get_data(data)

        condition["gas"] = rt["Accel"] / 255
        condition["brake"] = rt["Brake"] / 255
        condition["gear"] = rt["Gear"]
        condition["drive_mode"] = current_drive_mode

        APP.update_home(condition)

        if condition["stop"]:
            sys.exit()

        APP.update()
        APP.update_idletasks()

        if current_drive_mode == "M":
            continue

        if rt["IsRaceOn"] == 0:
            continue

        analyzeInput()
        makeDecision()

        time.sleep(0.005)


if __name__ == "__main__":

    APP = GUI.FHRG_GUI(
        condition=condition,
        VERSION=VERSION
    )

    APP.check_update()

    main()

    sys.exit()
