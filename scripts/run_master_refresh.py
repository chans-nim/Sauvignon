from src.master.download_master import main as d
from src.master.parse_kospi_mst import main as p1
from src.master.parse_kosdaq_mst import main as p2
from src.master.build_universe import main as b

if __name__ == "__main__":
    d(); p1(); p2(); b()
