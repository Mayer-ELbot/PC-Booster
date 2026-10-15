import sys

from pc_booster.ui import PCBooster


if __name__ == "__main__":
    PCBooster(start_in_background="--background" in sys.argv).mainloop()
