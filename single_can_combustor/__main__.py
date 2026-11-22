import sys

from gas_turbine_cycle_app.design_tools import main


if __name__ == "__main__":
    main(["combustor", *sys.argv[1:]])
