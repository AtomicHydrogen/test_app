import matplotlib.pyplot as plt
import numpy as np
import rpyc

import time

from qudi.util.network import netobtain

temp = rpyc.Service()
conn = rpyc.connect(host='localhost',config={'allow_all_attrs': True,
                                               'allow_setattr': True,
                                               'allow_delattr': True,
                                               'allow_pickle': True,
                                               'sync_request_timeout': 3600},
                                                port=18861,
                                                service=temp)

pulsed_measurement_logic = conn.root.get_namespace_dict()['pulsed_measurement_logic']
pulsed_master_logic      = conn.root.get_namespace_dict()['pulsed_master_logic']
sequence_generator_logic = conn.root.get_namespace_dict()['sequence_generator_logic']

def get_random_seed(length): # generate 160 bit string for qr or 8 bit string for random number
    pulsed_measurement_logic.raw_data_logging = True
    measurement_time = 15 if length == 160 else 1.75 # in seconds, How long to generate random bits for

    def wait_for_unlock(qudi_module, timeout = 30):
        if qudi_module.module_state() != 'locked':
            # Wait till module is locked
            time.sleep(1)
        t = 0
        while qudi_module.module_state() == 'locked':
            try:
                time.sleep(0.1)
                t += 1
                if t*0.1 > timeout:
                    raise TimeoutError('Waiting took to long')
            except KeyboardInterrupt:
                print('Aborted wait for unlock')
                break

    def pulsed_measurement_for(duration = 30):
        print('Measurement Started')
        pulsed_measurement_logic.start_pulsed_measurement()

        try:
            wait_for_unlock(pulsed_measurement_logic, duration)
        except TimeoutError:
            pulsed_measurement_logic.stop_pulsed_measurement()
        print('Measurement Done')

    pulsed_measurement_logic.set_timer_interval(0.2) # set analysis timer interval to 200ms

    time.sleep(0.25)
    # wait_for_unlock(pulsed_master_logic)

    rng_sequence_name = "rng3"
    if pulsed_master_logic.loaded_asset[0] != rng_sequence_name:
        pulsed_master_logic.generate_predefined_sequence(rng_sequence_name, kwarg_dict=dict(num_norm_pulses=50), sample_and_load = True)
        #sample and load the rng sequence

        print('Sampling started')
        wait_for_unlock(sequence_generator_logic)
        print('Sampling done')

    # HERE YOU CAN INCREASE TIME FOR MORE DATA
    pulsed_measurement_for(measurement_time)

    pulsed_measurement_logic.raw_data_logging = False

    ## Data has been acquired, next cell generates the random numbers from it
    # We could in principle also do this in qudi directly but thats currently rather invasive

    ### Pulse Extraction (Use offset_to_sampling_info since conv_deriv will not work for only few counts)
    t_offset_method = pulsed_measurement_logic._pulseextractor._ungated_extraction_methods['offset_to_sampling_info']
    ex_kwargs = {'t_offset': 1.5e-07}

    ### Pulse analysis method
    sum_analysis_method = pulsed_measurement_logic._pulseanalyzer._analysis_methods['sum']
    ana_kwargs = dict(signal_start=6.72e-08, signal_end=4.384e-07)

    differences = pulsed_measurement_logic._diff_log[:-1]
    # The last entry has less photons somehow.


    rn_bits = np.zeros(len(differences))

    for idx, dif in enumerate(differences):

        if not dif.any():
            # print(f"skipping dif at index {idx}")
            continue

        laser_pulses = t_offset_method(dif, **ex_kwargs)['laser_counts_arr']
        summed_analysis = sum_analysis_method(laser_pulses, **ana_kwargs)[0]

        rn_bits[idx] = np.mean(netobtain(summed_analysis)[:-1]) <= netobtain(summed_analysis)[-1]

    print(f"Number of random bits: {len(pulsed_measurement_logic._diff_log[:-1])} in {pulsed_measurement_logic.elapsed_time:.1f} s")
    print(f"=> {(len(pulsed_measurement_logic._diff_log)/ pulsed_measurement_logic.elapsed_time):.2f} bits/s\n")

    # Count occurrences
    unique, counts = np.unique(rn_bits, return_counts=True)

    # Convert to percentage
    total = len(rn_bits)
    percentages = {int(key): (count / total) * 100 for key, count in zip(unique, counts)}
    print("Percentage of 0s and 1s:", percentages)

    print(len(rn_bits))
    print(unique, counts)


    return "".join(str(int(x)) for x in rn_bits[:160]) if length == 160 else "".join(str(int(x)) for x in rn_bits[:8])