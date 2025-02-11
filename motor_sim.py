"""Nitrous oxide vapour pressure fed hybrid rocket motor firing simulator"""
__copyright__ = """

    Copyright 2019 Joe Hunt

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
### Joe Hunt updated 20/06/19 ###
### All units SI unless otherwise stated ###

import csv
import numpy as np
import matplotlib.pyplot as plt
import hybrid_functions as motor


###############################################################################
# Input parameters
###############################################################################

VOL_TANK = 0.047 ** 2 * 3.14 * 0.7 # tank volume (m^3)
HEAD_SPACE = 0.15              # initial vapour phase proportion

# Number of injector orifices
NUM_INJ = 12                 # number of primary injector orifices
DIA_INJ = 0.0015             # diameter of primary injector orifices (m)

DIA_PORT = 0.04              # diameter of fuel port (m)
LENGTH_PORT = 0.7            # length of fuel port (m)
DIA_FUEL = 0.07              # Outside diameter of fuel grain (m)
C_STAR_EFFICIENCY = 0.95      # Ratio between actual and theoretical
                              # characteristic velocity

DIA_THROAT = 0.02           # nozzle throat diameter (m)
NOZZLE_EFFICIENCY = 0.97      # factor by which to reduce thrust coefficient
NOZZLE_AREA_RATIO = 3.5       # ratio of nozzle exit area to throat area

DIA_FEED = 0.01               # feed pipe diameter (m)
LENGTH_FEED = 0.2             # feed pipe length (m)
VALVE_MODEL_TYPE = 'ball'     # either 'kv' or 'ball' (models as thick orifice)
KV_VALVE = 5                  # used if VALVE_MODEL_TRY='kv'
DIA_VALVE = 0.015             # used if VALVE_MODEL_TRY='ball'
LENGTH_VALVE = 0.08           # used if VALVE_MODEL_TRY='ball'

DENSITY_FUEL = 1000            # solid fuel density (kg m^-3)
REG_COEFF = 1.157E-4	      # regression rate coefficient (usually 'a' in
                              #                              textbooks)
REG_EXP = 0.331		          # regression rate exponent (usually 'n' in
                              #                           textbooks)

PRES_EXTERNAL = 101325        # external atmospheric pressure at test site (Pa)
temp = 25 + 273.15            # initial tank temperature (K)



# Create pipes for pipe-like things
port   = motor.Pipe(DIA_PORT, LENGTH_PORT)
fuel   = motor.Pipe(DIA_FUEL)
throat = motor.Pipe(DIA_THROAT)
feed   = motor.Pipe(DIA_FEED, LENGTH_FEED)
valve  = motor.Pipe(DIA_VALVE, LENGTH_VALVE)


###############################################################################
# Initialize simulation
###############################################################################

if 'dracula' in plt.style.available:
    plt.style.use('dracula')
else:
    plt.style.use('seaborn-whitegrid')

dt = 1e-2  # time step (s)

#open propep data file
propep_file = open('data/L_Nitrous_S_HDPE.propep', 'r')
propep_data = propep_file.readlines()

#open compressibility_data csv file
with open('data/n2o_compressibility_factors.csv') as csvfile:
    compressibility_data = csv.reader(csvfile)
    pdat, zdat = motor.compressibility_read(compressibility_data)

# assign initial values
vapz_lag = 0
time = 0
mdotox = 0
impulse = 0
gamma_N2O = 1.31
blowdown_type = 'liquid'

# temperature dependent properties
lden, vden, hl, hg, cp, vap_pres, ldynvis = motor.thermophys(temp)

hv = hg - hl # spec heat of vapourization
pres_cham = PRES_EXTERNAL

#calculate initial propellant masses
lmass = VOL_TANK * (1 - HEAD_SPACE) * lden
vmass = VOL_TANK * HEAD_SPACE * vden
fuel_mass = (fuel.A - port.A) * port.l * DENSITY_FUEL
tmass = lmass + vmass

(  # create empty lists to fill with output data
    time_data,
    vap_pres_data,
    pres_cham_data,
    thrust_data,
    gox_data,
    prop_mass_data,
    manifold_pres_data,
    gamma_data,
    throat_data,
    nozzle_efficiency_data,
    exit_pressure_data,
    area_ratio_data,
    of_data,
    regression_data,
    port_diameter_data,

    # additional properties needed for the 6DOF simulation
    vden_data, vmass_data,
    lden_data, lmass_data,
    fuel_mass_data
) = [[] for _ in range(20)]

# print initial conditions
print(f"""
Initial conditions:
    time: {time:.4f} s
    tank temperature: {temp-273.15:.2f} C
    lmass: {lmass:.4f} kg
    vmass: {vmass:.4f} kg
    vap_pres {vap_pres:.4f} Pa
    fuel thickness: {0.5 * (DIA_FUEL-DIA_PORT):.4f} m
    fuel mass {fuel_mass:.4f} kg
""")

###############################################################################
# Simulation loop
###############################################################################

while True:
    time += dt  # increment time

    # calculate feed system losses (only attemped for liquid phase)
    if mdotox > 0 and lmass > 0:
        flow_speed = mdotox / (lden * feed.A)
        entry_loss = 0.5 * lden * (flow_speed ** 2)  # loss at tank entry

        reynolds = lden * flow_speed * feed.d / ldynvis
        f = motor.Nikuradse(reynolds)

        # loss in pipe
        vis_pdrop = 0.25 * f * lden * (flow_speed**2) * feed.l / feed.d

        if VALVE_MODEL_TYPE == 'ball':
            #valve loss from full bore ball valve modelled as thick orifice
            valve_loss = (0.5 * lden * flow_speed * flow_speed
                          * motor.ball_valve_K(reynolds, feed.d, valve.d,
                                               valve.l))

        elif VALVE_MODEL_TYPE == 'kv':
            valve_loss = (1.296e9 * mdotox * mdotox /
                          (lden * KV_VALVE * KV_VALVE))

        # sum pressure drops
        manifold_pres = vap_pres - entry_loss - valve_loss - vis_pdrop
    else:
        manifold_pres = vap_pres


    #calculate injector pressure drop
    inj_pdrop = manifold_pres - pres_cham

    if inj_pdrop < 0.15 and time > 0.5:
        print(f'FAILURE: Reverse flow occurred at t={time} s')
        break

    # model tank emptying

    if blowdown_type == 'liquid':
        # liquid phase blowdown

        mdotox = NUM_INJ * motor.dyer_injector(
            pres_cham, DIA_INJ, lden, inj_pdrop,
            hl, manifold_pres, vap_pres
        )

        # find new mass of tank contents after outflow
        tmass -= mdotox * dt

        # liquid mass prior to vaporization
        lmass_pre_vap = lmass - (mdotox * dt)

        # lmass post vaporization
        lmass_post_vap = (vden * VOL_TANK - tmass) / (vden / lden - 1)

        if lmass_pre_vap < lmass_post_vap:  # check for liquid depletion
            print(f'starting vapour blowdown, vapour mass is {vmass+lmass:.4f} kg')
            print(f'injector pressure drop at liquid depletion was '
                  f'{100 * inj_pdrop / pres_cham:.4f}%')

            blowdown_type = 'vapour'
            lmass = 0
            vmass = tmass

            # define tank parameters at liquid depletion
            vmass_ld, temp_ld, vden_ld, vap_pres_ld = (vmass, temp,
                                                       vden, vap_pres)

            Z_ld = np.interp(motor.thermophys(temp_ld)[5], pdat, zdat)

        else:  # continue with liquid blowdown stage
            lmass = lmass_post_vap
            vapz = lmass_pre_vap - lmass  # mass vapourized

            # add 1st order lag of 0.15s to model vaporization time
            vapz_lag = dt / 0.15 * (vapz - vapz_lag) + vapz_lag
            vmass = tmass - lmass

            # update nitrous thermophysical properties given new temperature
            temp -= vapz_lag * hv / lmass / cp
            lden, vden, hl, hg, cp, vap_pres, ldynvis = motor.thermophys(temp)
            hv = hg - hl  # spec heat of vapourization

    else:
        # vapour phase blowdown

        # calculations for injector orifices
        mdotox = NUM_INJ * motor.vapour_injector(DIA_INJ, vden, inj_pdrop)
        vmass -= dt * mdotox  # sum flow from 3 types of orifice

        # find current tank vapour parameters
        Z2 = motor.Z2_solve(temp_ld, Z_ld, vmass_ld, vmass, gamma_N2O,
                            zdat, pdat)

        if Z2 == 'numerical instability':
            print('vapour depleted: finishing motor simulation')
            break

        #isentropic assumption
        temp = temp_ld * pow(Z2 * vmass / (Z_ld * vmass_ld), gamma_N2O-1)
        vap_pres = vap_pres_ld * pow(temp / temp_ld, gamma_N2O / (gamma_N2O-1))
        vden = vden_ld * pow(temp / temp_ld, 1 / (gamma_N2O-1))

    # check for excessive mass flux
    if mdotox / port.A > 600:
        print(f'Failure: oxidizer flux too high: {mdotox / port.A:.2f}')
        # break

    # fuel port calculation
    rdot = REG_COEFF * pow(mdotox/port.A, REG_EXP)
    mdotfuel = rdot * DENSITY_FUEL * np.pi * port.d * port.l

    port.d += 2*rdot*dt

    if port.d > fuel.d: #check for depleted fuel grain
        print("fuel depleted")
        break

    fuel_mass = (fuel.A - port.A) * port.l * DENSITY_FUEL


    # lookup characteristic velocity using previous
    # pres_cham and current OF from propep data
    c_star = (motor.c_star_lookup(pres_cham, mdotox / mdotfuel, propep_data)
              * C_STAR_EFFICIENCY)

    # calculate current chamber pressure
    pres_cham = (mdotox + mdotfuel) * c_star / throat.A

    # lookup ratio of specific heats from propep data file
    gamma = motor.gamma_lookup(pres_cham, mdotox/mdotfuel, propep_data)


    # performance calculations
    # find nozzle exit static pressure
    mach_exit = motor.mach_exit(gamma, NOZZLE_AREA_RATIO)
    pres_exit = pres_cham * pow(1 + (gamma - 1) * mach_exit * mach_exit * 0.5,
                                -gamma / (gamma - 1))


    # motor performance calculations
    thrust = NOZZLE_EFFICIENCY * (
        throat.A * pres_cham * np.sqrt(
            2 * gamma**2 / (gamma - 1)
            * pow(2 / (gamma + 1), (gamma + 1) / (gamma - 1))
            * (1 - pow(pres_exit / pres_cham, 1 - 1 / gamma))
        ) + (pres_exit - PRES_EXTERNAL) * throat.A * NOZZLE_AREA_RATIO)


    #update data lists
    time_data.append(time)
    vap_pres_data.append(vap_pres)
    pres_cham_data.append(pres_cham)
    manifold_pres_data.append(manifold_pres)
    thrust_data.append(thrust)
    gox_data.append(mdotox / port.A)
    prop_mass_data.append(lmass + vmass + fuel_mass)
    gamma_data.append(gamma)
    throat_data.append(DIA_THROAT)
    nozzle_efficiency_data.append(NOZZLE_EFFICIENCY)
    exit_pressure_data.append(pres_exit)
    area_ratio_data.append(NOZZLE_AREA_RATIO)
    of_data.append(mdotox/mdotfuel)
    regression_data.append(rdot)
    port_diameter_data.append(port.d)

    #additional data for the 6DOF simulation
    vmass_data.append(vmass)
    vden_data.append(vden)
    lden_data.append(lden)
    lmass_data.append(lmass)
    fuel_mass_data.append(fuel_mass)



###############################################################################
# Print and plot results
###############################################################################

#print final results
print("\nFinal conditions:\ntime:", time, "s\ntank temperature:", temp-273.15,
      "C\nlmass:", lmass, "kg\nvmass:", vmass, "kg\nvap_pres:", vap_pres,
      'Pa\nfuel thickness:', (DIA_FUEL-port.d)/2, 'm\nfuel mass', fuel_mass,
      'kg')

impulse = dt * sum(thrust_data[:len(time_data)])

print('\nPerformance results:\nInitial thrust:', thrust_data[int(0.5/dt)],
      'N\nmean thrust:', np.mean(thrust_data), 'N\nimpulse:', impulse,
      'Ns\nmean Isp:', impulse/(prop_mass_data[0]-fuel_mass)/9.81)
print(
f"""\n Midburn Results:
O/F: {of_data[len(of_data) // 2]})
Regression: {regression_data[len(regression_data) // 2]}
Pressure margin: {np.min( ( np.array(manifold_pres_data) - np.array(pres_cham_data) ) / np.array(pres_cham_data) )}
""")

#plot pressures
plt.figure(figsize=(8.5, 7))
plt.subplot(221)
plt.plot(time_data, vap_pres_data, 'C0', label='Tank pressure')
plt.plot(time_data, pres_cham_data, 'C5', label='Chamber pressure')
plt.plot(time_data, manifold_pres_data, 'C2', label='Injector manifold pressure')
plt.ylabel('Pressure (Pa)')
plt.ylim(0, max(vap_pres_data)*1.3)
plt.xlabel('Time (s)')
plt.ylabel('Pressure (Pa)')
plt.legend()
plt.tight_layout()

#plot thrust
plt.subplot(222)
plt.plot(time_data, thrust_data)
plt.xlabel('Time (s)')
plt.ylabel('thrust (N)')
plt.ylim(0, max(thrust_data)*1.3)
plt.tight_layout()

#plot massflux
plt.subplot(223)
plt.plot(time_data, gox_data, 'C6')
plt.xlabel('Time (s)')
plt.ylabel('Oxidizer mass flux ($kg s^{-1} m^{-2}$)')
plt.ylim(0, max(gox_data)*1.3)
plt.tight_layout()

#plot O/F
plt.subplot(224)
plt.plot(time_data, of_data, 'C2')
plt.xlabel('Time (s)')
plt.ylabel('O/F Ratio')
plt.ylim(0, max(of_data)*1.3)
plt.tight_layout()

plt.show()

###############################################################################
# generate motor_output.csv for trajectory simulation
###############################################################################
with open("motor_out.csv", "w", newline='') as motor_file:
    motor_file.truncate()
    motor_write = csv.writer(motor_file)
    motor_write.writerow([
        'Time',
        'Propellant mass (kg)',
        'Chamber pressure (Pa)',
        'Throat diameter (m)',
        'Nozzle inlet gamma',
        'Nozzle efficiency',
        'Exit static pressure (Pa)',
        'Area ratio',
        'Vapour Density (kg/m^3)',
        'Vapour Mass (kg)',
        'Liquid Density (kg/m^3)',
        'Liquid Mass (kg)',
        'Solid Fuel Mass (kg)',
        'Solid Fuel Density (kg/m^3)',
        'Solid Fuel Outer Diameter (m)',
        'Solid Fuel Length (m)'
    ])

    for i, _ in enumerate(time_data):
        motor_write.writerow([time_data[i], prop_mass_data[i],
                              pres_cham_data[i],
                              throat_data[i], gamma_data[i],
                              nozzle_efficiency_data[i], exit_pressure_data[i],
                              area_ratio_data[i],
                              vden_data[i], vmass_data[i],
                              lden_data[i], lmass_data[i], fuel_mass_data[i],
                              DENSITY_FUEL, DIA_FUEL, LENGTH_PORT])

    motor_write.writerow([time_data[-1] + dt, fuel_mass, pres_cham_data[-1],
                          throat_data[-1], gamma_data[-1],
                          nozzle_efficiency_data[-1], exit_pressure_data[-1],
                          area_ratio_data[-1],
                          vden_data[-1], 0,
                          lden_data[-1], lmass_data[-1], fuel_mass_data[-1],
                          DENSITY_FUEL, DIA_FUEL, LENGTH_PORT])


###############################################################################
# generate a RASP motor file for RAS Aero
###############################################################################

RASP_DIA = 160      # motor diameter in mm
RASP_LENGTH = 3000  # motor length in mm
RASP_DRY = 40       # motor dry mass in kg

with open("hybrid.eng", "w+") as rasp_file:

    rasp_file.write(';\n')
    rasp_file.write(f'Pulsar {RASP_DIA} {RASP_LENGTH} P'
                    f' {prop_mass_data[0]:.2f}'
                    f' {prop_mass_data[0] + RASP_DRY:.2f} CUSF\n')

    for i in range(31):
        t = int(i * len(time_data) / 31)
        rasp_file.write(
                f'\t{float(time_data[t]):.2f} {float(thrust_data[t]):.2f}\n')

    rasp_file.write(f'\t{float(time_data[-1]):.2f} 0.0\n')
    rasp_file.write(';')