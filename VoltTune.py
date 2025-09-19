import os
import numpy as np

def get_dv_dp(v_error, p_history, center=0):
    """
    Compute ΔV and ΔP matrices for sensitivity estimation.
    
    Parameters:
        v_error (np.ndarray): Voltage measurements [n_buses x n_time_steps]
        p_history (np.ndarray): Power values [n_buses x n_time_steps]
        center (int): Index of the reference node for voltage alignment
    
    Returns:
        dv (np.ndarray): Voltage differences (excluding reference)
        dp (np.ndarray): Power differences (excluding reference)
    """
    v_error -= v_error[center]
    delta_v = v_error[:, 1:] - v_error[:, :-1]
    delta_p = p_history[:, 1:] - p_history[:, :-1]
    dv = delta_v[1:]  # remove reference node
    dp = delta_p[1:]  # remove reference node
    return dv, dp

def compute_sensitivity_matrix(dv, dp):
    """
    Compute the sensitivity matrix S such that: ΔV = S × ΔP
    
    Parameters:
        dv (np.ndarray): Voltage differences
        dp (np.ndarray): Power differences
    
    Returns:
        S (np.ndarray): Sensitivity matrix (negative by design)
    """
    S, _, _, _ = np.linalg.lstsq(dp.T, dv.T, rcond=None)
    return -np.abs(S)  # enforce negative sensitivities

def load_data_from_txt(filename):
    """
    Load voltage and power history data from a .txt file.
    
    Assumes alternating rows: voltage, power, voltage, power, etc.

    Parameters:
        filename (str): Path to the .txt file
    
    Returns:
        voltages (np.ndarray): Voltage matrix
        powers (np.ndarray): Power matrix
    """
    with open(filename, "r") as f:
        lines = f.readlines()[2:]  # skip header lines

    data = [list(map(float, line.strip().split('\t'))) for line in lines]
    data = np.array(data).T
    voltages = data[::2]  # even-indexed rows
    powers = data[1::2]   # odd-indexed rows
    return voltages, powers

def load_flexibility_file(path):
    """
    Load flexibility values and current voltages from file.
    
    File format: [V_now, flex_reduction, flex_injection]

    Parameters:
        path (str): Path to the flexibility input file

    Returns:
        V_now (np.ndarray): Current voltages
        flex_red (np.ndarray): Flexibility to reduce load
        flex_inj (np.ndarray): Flexibility to inject power (negated)
    """
    data = np.loadtxt(path)
    voltages = data[:, 0]
    flex_red = data[:, 1]
    flex_inj = -data[:, 2]  # make injection positive
    return voltages, flex_red, flex_inj

def greedy_voltage_correction(S_full, V_now, flex_red, flex_inj, Vmin=207, Vmax=250):
    """
    Iteratively correct voltage violations using greedy allocation of flexibility.

    Parameters:
        S_full (np.ndarray): Full sensitivity matrix
        V_now (np.ndarray): Current voltages
        flex_red (np.ndarray): Load reduction flexibility
        flex_inj (np.ndarray): Power injection flexibility
        Vmin (float): Minimum acceptable voltage
        Vmax (float): Maximum acceptable voltage

    Returns:
        dp_applied (np.ndarray): Total power adjustments applied at each node
        V_now (np.ndarray): Final corrected voltages
    """
    # Remove reference node if present
    S = S_full[:, :-1] if S_full.shape[1] > S_full.shape[0] else S_full.copy()
    V_now = V_now.copy()
    flex_red = flex_red.copy()
    flex_inj = flex_inj.copy()
    n = len(V_now)
    dp_applied = np.zeros(n)

    # Pre-check feasibility for each violating node
    over_idx = np.where(V_now > Vmax)[0]
    under_idx = np.where(V_now < Vmin)[0]
    all_viol = np.concatenate([over_idx, under_idx])

    for idx in all_viol:
        direction = 1 if V_now[idx] > Vmax else -1
        dp_full = np.array([
            flex_inj[j] if direction == 1 else -flex_red[j]
            for j in range(n)
        ])
        if S.shape[1] != dp_full.shape[0]:
            print("Dimension mismatch between sensitivity matrix and flexibility vector.")
            return None, None

        delta_V = S[idx] @ dp_full
        V_test = V_now[idx] + delta_V

        if direction == -1 and V_test > Vmax:
            print(f"Cannot reduce voltage at node {idx} below {Vmax} V using full flexibility.")
            return None, None
        if direction == 1 and V_test < Vmin:
            print(f"Cannot raise voltage at node {idx} above {Vmin} V using full flexibility.")
            return None, None

    # Greedy correction loop
    iteration = 0
    while True:
        over_idx = np.where(V_now > Vmax)[0]
        under_idx = np.where(V_now < Vmin)[0]

        if len(over_idx) == 0 and len(under_idx) == 0:
            break  # all voltages within limits

        # Find worst violation
        all_viol = np.concatenate([over_idx, under_idx])
        viol_mags = np.abs(np.concatenate([
            V_now[over_idx] - Vmax,
            Vmin - V_now[under_idx]
        ]))
        worst_idx = all_viol[np.argmax(viol_mags)]
        direction = -1 if V_now[worst_idx] > Vmax else 1

        # Rank nodes by sensitivity impact
        impacts = S[worst_idx] * direction
        impact_order = np.argsort(-np.abs(impacts))  # descending impact

        for j in impact_order:
            s_ij = S[worst_idx, j]
            if direction * s_ij <= 0:
                continue  # ignore ineffective nodes

            needed = (
                (Vmax - V_now[worst_idx]) / s_ij
                if direction == -1
                else (Vmin - V_now[worst_idx]) / s_ij
            )
            apply = np.clip(needed, -flex_red[j], flex_inj[j])
            if apply == 0:
                continue

            # Apply the correction
            dp_iter = np.zeros(n)
            dp_iter[j] = apply
            delta_V = S @ dp_iter

            V_now += delta_V
            dp_applied += dp_iter
            flex_red[j] -= max(0, -apply)
            flex_inj[j] -= max(0, apply)
            break  # only one action per iteration

        iteration += 1
        if iteration > 1000:
            print("Warning: Too many iterations. Possible infinite loop.")
            break

    return dp_applied, V_now

def main():
    """
    Main entry point of the script.
    
    Loads environment variables, processes input data, computes the sensitivity matrix,
    and applies greedy voltage correction using flexibility.
    """
    np.set_printoptions(threshold=np.inf, suppress=True)

    # Load environment variables
    history_file = os.environ.get("HISTORIC_FILE")
    input_file = os.environ.get("INPUT_FILE")

    if history_file is None or input_file is None:
        print("Error: Environment variables HISTORIC_FILE and INPUT_FILE must be set.")
        print("Example (PowerShell):")
        print('  $env:HISTORIC_FILE = "database/historic_31.txt"')
        print('  $env:INPUT_FILE = "input/input.txt"')
        return

    # Load and process data
    voltages, powers = load_data_from_txt(history_file)
    dv, dp = get_dv_dp(voltages, powers, center=0)
    S = compute_sensitivity_matrix(dv, dp)
    V_now, flex_red, flex_inj = load_flexibility_file(input_file)

    # Run correction
    dp_corrected, V_corrected = greedy_voltage_correction(S, V_now, flex_red, flex_inj)

    if dp_corrected is None or V_corrected is None:
        print("Unable to correct voltages with available flexibility.")
        return

    # Final results
    print("\n--- Final Results ---")
    print("Applied power corrections (Delta P per node):")
    print(np.round(dp_corrected, 4))

    print("\nCorrected voltages (in volts):")
    print(np.round(V_corrected, 2))

    violations = np.sum((V_corrected < 207) | (V_corrected > 250))
    print(f"\nNumber of nodes still violating voltage limits: {violations}")

if __name__ == "__main__":
    main()
