from fastapi import FastAPI, UploadFile, File, Query
from pydantic import BaseModel
import numpy as np
from VoltTune import (
    get_dv_dp,
    compute_sensitivity_matrix,
    load_data_from_txt,
    load_flexibility_file,
    greedy_voltage_correction,
)

app = FastAPI(
    title="VoltTune API",
    description="API for computing voltage correction using flexibility and sensitivity matrices.",
    version="1.0.0"
)




# Define structured response model for better docs
class ComputeResponse(BaseModel):
    dp_applied: list[float]
    V_corrected: list[float]
    violations: int

@app.post(
    "/voltage_correction_nodes",
    response_model=ComputeResponse,
    summary="Compute voltage correction from input files",
    description=(
        "Upload two input files to compute voltage corrections:\n\n"
        "- **historic.txt**: time-series of voltages and powers per node.\n"
        "- **input.txt**: current voltages (`V_now`), flexibility for load reduction "
        "(`flex_red`), and flexibility for power injection (`flex_inj`).\n\n"
        "Optional query parameters `Vmin` and `Vmax` set the acceptable voltage range."
    ),

    tags=["Voltage Correction"]
)
async def compute_files(
    historic: UploadFile = File(..., description="Historic File [.txt]"),
    input_file: UploadFile = File(..., description="Flexibility File [.txt] "),
    Vmax: float = Query(250, description="Maximum Acceptable Voltage"),
    Vmin: float = Query(207, description="Minimum Acceptable Voltage")
    
):
    # Save uploaded files temporarily
    with open("temp_historic.txt", "wb") as f:
        f.write(await historic.read())
    with open("temp_input.txt", "wb") as f:
        f.write(await input_file.read())

    # Process with your existing pipeline
    voltages, powers = load_data_from_txt("temp_historic.txt")
    dv, dp = get_dv_dp(voltages, powers, center=0)
    S = compute_sensitivity_matrix(dv, dp)
    V_now, flex_red, flex_inj = load_flexibility_file("temp_input.txt")

    dp_corrected, V_corrected = greedy_voltage_correction(
        S, V_now, flex_red, flex_inj, Vmin=Vmin, Vmax=Vmax
    )

    return ComputeResponse(
        dp_applied=dp_corrected.tolist(),
        V_corrected=V_corrected.tolist(),
        violations=int(np.sum((V_corrected < Vmin) | (V_corrected > Vmax)))
    )
