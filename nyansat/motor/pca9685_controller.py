
import pca9685
import math
import time
import machine
import _thread

from .motor_controller import MotorController

class Pca9685Controller(MotorController):
    """Controller for the PCA9685 servomotor PWM mux driver for antenny."""

    #combo to run both 4 servos and 4 motors
    _DC_MOTORS = ((8, 9, 10), (13, 12, 11), (2, 3, 4), (7, 6, 5))
    SERVOS = [0, 1, 14, 15]

    def __init__(self, i2c: machine.I2C, address: int = 0x40, freq: int = 0x50,
                 min_us: int = 600, max_us: int = 2400, degrees: int = 180):
        """Initialize the servo driver from a given micropython machine.I2C
        object and other parameters.
        
        address - I2C address of the device
        """
        self.period = 1000000 / freq
        self.min_duty = self._us2duty(min_us)
        self.max_duty = self._us2duty(max_us)
        self._degrees = [degrees, degrees]
        self.freq = freq
        self.pca9685 = pca9685.PCA9685(i2c, address)
        self.pca9685.freq(freq)
        
        # Using timer 0 for interrupt-based movement
        self.move_timer = machine.Timer(0)
        self._move_data = None
        self.move_lock = _thread.allocate_lock()

    def degrees(self, index: int) -> float:
        """Return the position in degrees of the servo with the given index."""
        return self._degrees[index]

    def _set_degrees(self, index: int, value: int) -> None:
        """Set the degrees variable to. This function is to provide an
        abstracted wrapper around the naked servo.degrees[i] access in Antenny's
        test_az/save_ant_calib etc. functions
        """
        self._degrees[index] = value

    def _us2duty(self, value):
        return int(4095 * value / self.period)

    def position(self, index, degrees=None, radians=None, us=None, duty=None):
        """Set the servo with the given index to move to a specified position,
        given by either degrees, radians, us, or duty.
        """
        assert index in self.SERVOS
        
        span = self.max_duty - self.min_duty
        if degrees is not None:
            duty = self.min_duty + span * degrees / self._degrees[index]
        elif radians is not None:
            duty = self.min_duty + span * radians / math.radians(self.degrees)
        elif us is not None:
            duty = self._us2duty(us)
        elif duty is not None:
            pass
        else:
            return self.pca9685.duty(index)
        duty = min(self.max_duty, max(self.min_duty, int(duty)))
        self.pca9685.duty(index, duty)
    
    def __move_one(self, timer):
        index, end, step = self._move_data
        cur = self.pca9685.duty(index)
        if cur != end:
            self.pca9685.duty(index, cur + step)
        else:
            self.move_lock.release()
            timer.deinit()

    def smooth_move(self, index, degrees, delay):
        """
        Basically a spin-lock here at the top. acquire() is supposed to wait
        for the lock if it is not available but it hangs the system when I 
        tried it.
        """
        while self.move_lock.locked():
            pass
        self.move_lock.acquire()
        assert index in self.SERVOS
        span = self.max_duty - self.min_duty
        duty = self.min_duty + span * degrees / self._degrees[index]
        start = self.pca9685.duty(index)
        end = min(self.max_duty, max(self.min_duty, int(duty))) 
        step = -1 if start > end else 1
        self._move_data = [index, end, step]
        self.move_timer.init(period=delay, mode=machine.Timer.PERIODIC, callback=self.__move_one)
        return duty

    def release(self, index):
        self.pca9685.duty(index, 0)

    def _pin(self, pin, value=None):
        if value is None:
            return bool(self.pca9685.pwm(pin)[0])
        if value:
            self.pca9685.pwm(pin, 4096, 0)
        else:
            self.pca9685.pwm(pin, 0, 0)

    def speed(self, index, value=None):
        pwm, in2, in1 = self._DC_MOTORS[index]
        if value is None:
            value = self.pca9685.duty(pwm)
            if self._pin(in2) and not self._pin(in1):
                value = -value
            return value
        if value > 0:
            # Forward
            self._pin(in2, False)
            self._pin(in1, True)
        elif value < 0:
            # Backward
            self._pin(in1, False)
            self._pin(in2, True)
        else:
            # Release
            self._pin(in1, False)
            self._pin(in2, False)
        self.pca9685.duty(pwm, abs(value))

    def brake(self, index):
        pwm, in2, in1 = self._DC_MOTORS[index]
        self._pin(in1, True)
        self._pin(in2, True)
        self.pca9685.duty(pwm, 0)