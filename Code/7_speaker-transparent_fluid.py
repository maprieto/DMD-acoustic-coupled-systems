"""
Fenics script to solve a the wave equation with rigid wall conditions in a one-dimensional domain
  rho*d2u/dt2-rho*c^2*d2u/dx2=f in [T_init,T_end]x[L0,L1]
  u(t,L0)=u(t,L1)=0 in [T_init,T_end]
  u(0,x)=u0(x) in [L0,L1]
  du/dt(0)=v0(x) in [L0,L1]
  The primal unknown is the displacement field, discretized with Lagrange P1 elements in each component
  The time-discretization is given by an implicit Newmark scheme
"""
import numpy as np
from dolfin import *
from mshr import *
import matplotlib.pylab as plt
import sys
import os.path
import csv
from scipy import stats
parameters['linear_algebra_backend'] = 'PETSc'

# ========== DEFINITIONS ===============================================================================================


# Definition of a progress bar
def drawProgressBar(time_, N_, barlen=50):
    sys.stdout.write("\r")
    progress = ""
    for i_ in range(barlen):
        if i_ < 1. * barlen * time_ / N_:
            progress += "="
        else:
            progress += " "
    sys.stdout.write("[%s] # Step = %i" % (progress, time_))
    sys.stdout.flush()

# Define exact solution (D'Alambert solution using the images principle and only 2 reflections taken into account)
class ExactSolutionImpulse(UserExpression):
    def __init__(self, t, u0, vel, L0, L1, **kwargs):
        self.t = t
        self.L0 = L0
        self.L1 = L1
        self.u0 = u0
        self.vel = vel
        super().__init__(**kwargs)

    def eval(self, values, x):
        L0 = self.L0
        L1 = self.L1
        t = self.t
        vel = self.vel
        u0 = self.u0
        values[0] = 0.5 * (u0(x[0] + vel * t) + u0(x[0] - vel * t) \
                           - u0(-x[0] - vel * t + 2. * L1) - u0(-x[0] + vel * t + 2. * L0) \
                           + u0(x[0] - vel * t - 2. * L0 + 2. * L1) + u0(x[0] + vel * t - 2. * L1 + 2. * L0))

    def value_shape(self):
        return ()

# Define exact solution (standing harmonic solution through Helmholtz problem)
class ExactSolutionHarmonic(UserExpression):
    def __init__(self, t, u0, v0, vel, L0, L1, omega, rho, **kwargs):
        self.t = t
        self.L0 = L0
        self.L1 = L1
        self.u0 = u0
        self.v0 = v0
        self.vel = vel
        self.omega = omega
        self.rho = rho
        self.k = omega / vel
        k = self.k
        self.A = 0.
        self.B = np.exp(-1j * k * L0)
        super().__init__(**kwargs)

    def eval(self, values, x):
        t = self.t
        omega = self.omega
        k = self.k
        A = self.A
        B = self.B
        values[0] = 1.*(np.exp(-1j * omega * t) * (A * np.exp(-1j * k * x[0]) + B * np.exp(1j * k * x[0]))).real

    def value_shape(self):
        return ()

class u_at_0_harmonic(UserExpression):
    def __init__(self, vel, L0, L1, omega, rho, **kwargs):
        self.L0 = L0
        self.L1 = L1
        self.vel = vel
        self.omega = omega
        self.rho = rho
        self.k = omega / vel
        k = self.k
        self.A = 0.
        self.B = np.exp(-1j * k * L0)
        super().__init__(**kwargs)

    def eval(self, values, x):
        k = self.k
        A = self.A
        B = self.B
        values[0] = 1.*(A * np.exp(-1j * k * x[0]) + B * np.exp(1j * k * x[0])).real

    def value_shape(self):
        return ()

class v_at_0_harmonic(UserExpression):
    def __init__(self, vel, L0, L1, omega, rho, **kwargs):
        self.L0 = L0
        self.L1 = L1
        self.vel = vel
        self.omega = omega
        self.rho = rho
        self.k = omega / vel
        k = self.k
        self.A = 0.
        self.B = np.exp(-1j * k * L0)
        super().__init__(**kwargs)

    def eval(self, values, x):
        omega = self.omega
        k = self.k
        A = self.A
        B = self.B
        values[0] = 1.*(-1j * omega * (A * np.exp(-1j * k * x[0]) + B * np.exp(1j * k * x[0]))).real

    def value_shape(self):
        return ()

# ========== PARAMETERS ================================================================================================

probIdStr = 'st-f_harm_' # Identification of the problem for save files' names

# Parameter values for the fluid
rho_fluid = 1.21  # mass density [kg/m^3]
vel_fluid = 343.  # sound speed [m/s]

# Mesh limits (unit interval)
L0 = 0.; L1 = 1.

omega_harm = 6.5 * np.pi * vel_fluid

# ========== SETUP =====================================================================================================

# Define boundary subdomains
speaker_boundary = CompiledSubDomain("on_boundary && near(x[0], L0)", L0=L0)
transparent_boundary = CompiledSubDomain("on_boundary && near(x[0], L1)", L1=L1)

# ========== COMPUTATION ===============================================================================================

def compute(Nele, dt, T_init, T_final, initCondId = 4, savePrefix=''):

    # Set number of elements in mesh and create mesh
    mesh = IntervalMesh(Nele, L0, L1)

    # Define initial data: Initial displacement
    if initCondId == 1: # Irregular
        u_at_0 = Expression('fabs(x[0]-a)<c ? b-pow(x[0]-a,2)/pow(c,2) : 0.', a=0.5 * (L0 + L1), b=1., c=(L1 - L0) / 20., degree=2)
    elif initCondId == 2: # Regular
        u_at_0 = Expression('fabs(x[0]-a)<b-tol ? exp(-1./(1.-pow((x[0]-a)/b,2)))/exp(-1.) : 0.', a=0.5 * (L0 + L1), b=(L1 - L0) / 20, tol=1e-3, degree=6)
    elif initCondId == 3: # Irregular v-2
        u_at_0 = Expression('fabs(x[0]-a)<c ? b*(1.-fabs(x[0]-a)/c) : 0.', a=0.5 * (L0 + L1), b=1., c=(L1 - L0) / 5., degree=1)
    elif initCondId == 6:  # Harmonic
        u_at_0 = u_at_0_harmonic(vel=vel_fluid, L0=L0, L1=L1, omega=omega_harm, rho=rho_fluid, degree=6)
    else: # Regular v-2
        u_at_0 = Expression('fabs(x[0]-a)<b-tol ? exp((2.*exp(-1./(fabs(x[0]-a)/b)))/(fabs(x[0]-a)/b-1.)) : 0.', a=0.5 * (L0 + L1), b=(L1 - L0) / 5., tol=1e-3, degree=6)

    # Define initial data: Define initial velocity
    if initCondId == 6:  # Harmonic
        v_at_0 = v_at_0_harmonic(vel=vel_fluid, L0=L0, L1=L1, omega=omega_harm, rho=rho_fluid, degree=6)
    else:  # Regular v-2
        v_at_0 = Expression('0.', degree=1)

    # Initialize exact solution
    if initCondId == 6: # Harmonic
        uex = ExactSolutionHarmonic(t=0, u0=u_at_0, v0=v_at_0, vel=vel_fluid, L0=L0, L1=L1, omega=omega_harm, rho=rho_fluid, degree=6)
    else:
        uex = ExactSolutionImpulse(t=0, u0=u_at_0, vel=vel_fluid, L0=L0, L1=L1, degree=2)

    # Load term (right-hand side in the wave equation)
    force = Expression('0.*t', t=0., degree=1)

    # Initialize mesh function for boundary
    boundary_markers = MeshFunction("size_t", mesh, mesh.topology().dim() - 1)
    boundary_markers.set_all(0)  # all faces (interior and exterior) are set to zero
    speaker_boundary.mark(boundary_markers, 1)  # rigid boundary
    transparent_boundary.mark(boundary_markers, 2)  # transparent boundary

    # Write to file the boundary markers (to check reference numbers)
    vtk_boundaries = File("results/boundaries.pvd")
    vtk_boundaries << boundary_markers

    # Initialize mesh function for the physical domain
    domain_markers = MeshFunction("size_t", mesh, mesh.topology().dim())
    domain_markers.set_all(0)  # all elements are set to zero

    # Write to file the subdomain markers (to check reference numbers)
    vtk_subdomains = File("results/subdomains.pvd")
    vtk_subdomains << domain_markers

    # Define new measures associated with each exterior boundaries
    dx = Measure('dx', domain=mesh, subdomain_data=domain_markers)
    ds = Measure('ds', domain=mesh, subdomain_data=boundary_markers)

    # Define function space (Lagrange 1st polynomials for each vector component)
    P1 = FiniteElement("Lagrange", mesh.ufl_cell(), 1)
    V = FunctionSpace(mesh, P1)

    # Define source term for the real and the imaginary part and null boundary conditions
    zero = Constant("0.0")
    harmOscilator = Expression('cos(omega*t)', omega=omega_harm, t=0., degree=1)

    # Define trial and test functions for the vector functional space V
    u = TrialFunction(V)
    w = TestFunction(V)

    # Define the part of the variational problem associated to stiffness (K)
    a_stiff = rho_fluid * pow(vel_fluid, 2) * inner(grad(u), grad(w)) * dx(0)

    # Define the part of the variational problem associated to inertia (M)
    a_mass = rho_fluid * inner(u, w) * dx(0)

    # Define the part of the variational problem associated to damping (C)
    a_damping = rho_fluid * vel_fluid * inner(u, w) * ds(2)

    # Assemble the matrices
    A_stiff = assemble(a_stiff)
    A_mass = assemble(a_mass)
    A_damping = assemble(a_damping)

    # Define null Dirichlet boundary conditions in the whole boundary for Newmark matrices
    bc = DirichletBC(V, harmOscilator, boundary_markers, 1)

    # Time discretization setting for Newmark's scheme
    beta = 0.25  # Newmark scheme coefficient
    gamma = 0.5  # Newmark scheme coefficient

    # Time marching quantities
    Nt = np.int((T_final - T_init) / dt)  # number of time steps
    t_vec = T_init + np.arange(0, Nt) * dt  # time array

    # Assemble the matrices associated to the Newmark method
    # A is the effective stiffness matrix  A = K + a0 * M + a1 * C
    A = A_stiff + (1. / (beta * dt ** 2)) * A_mass + gamma / (beta * dt) * A_damping

    # Apply boundary conditions
    bc.apply(A)

    # Compute LU factorization and definition for re-using
    solver = LUSolver(A)

    # Initial conditions
    u_0 = interpolate(u_at_0, V)
    v_0 = interpolate(v_at_0, V)

    # Displacement at time t=0
    y0 = Vector(u_0.vector())
    y1 = Vector(u_0.vector())
    # Velocity at time t=0
    v0 = Vector(v_0.vector())

    # Init vectors (their actual values will not be used)
    b_mass = Vector(u_0.vector())
    b_damping = Vector(u_0.vector())

    # Compute initial acceleration for the Newmark method
    b_force = interpolate(force, V).vector()
    A_stiff.mult(y0, b_mass)
    A_damping.mult(v0, b_damping)
    a0 = Vector(u_0.vector())
    # Solve M * a0 + C * v0 + K * u0 = f to get a0
    solve(A_mass, a0, b_force - b_mass - b_damping)

    # Functions to storage the values
    displacement = Function(V)
    velocity = Function(V)
    acceleration = Function(V)

    displacement.rename("u", "u")
    velocity.rename("v", "v")
    acceleration.rename("a", "a")

    # Store acceleration, velocity and displacement in a function
    displacement.vector().set_local(y0.get_local())
    velocity.vector().set_local(v0.get_local())
    acceleration.vector().set_local(a0.get_local())
    solution = interpolate(uex, V)
    solution.rename("uex", "uex")

    # xdmfFileName = 'results/rigid-rigid_fluid.xdmf'
    xdmfFileName = 'results/%s%s_IC_%i_Nele_%i_dt_%g.xdmf' % (savePrefix, probIdStr, initCondId, Nele, dt)

    xdmf_file = XDMFFile(xdmfFileName)
    xdmf_file.parameters['rewrite_function_mesh'] = False
    xdmf_file.parameters['functions_share_mesh'] = True

    # Storage initial data
    xdmf_file.write(displacement, 0.)
    xdmf_file.write(velocity, 0.)
    xdmf_file.write(acceleration, 0.)
    xdmf_file.write(solution, 0.)

    # Loop in time: Newmark method -------------------------------------------------------------------------------------
    for jt in range(Nt + 1):
        # Update time in loads and speaker boundary condition
        force.t = dt * (jt + 1)
        harmOscilator.t = dt * (jt + 1)
        # Compute the loads at time t+dt
        b_rhs = interpolate(force, V).vector()
        # Compute right-hand side for Newmark
        # Calculate effective loads at time t + dt
        # R^(t+dt) + M (a0 U^t + a2 U.^t + a3 U..^t) + C (a1 U^t + a4 U.^t + a5 U..^t)
        A_mass.mult(1.0 / (beta * dt ** 2) * y0 + 1.0 / (beta * dt) * v0 + (1.0 / (2 * beta) - 1.0) * a0, b_mass)
        A_damping.mult(gamma / (beta * dt) * y0 + (gamma / beta - 1.) * v0 + dt / 2. * (gamma / beta - 2.) * a0,
                       b_damping)
        b_vec = b_rhs + b_mass + b_damping
        # Apply boundary conditions
        bc.apply(b_vec)
        # Solve y_1
        solver.solve(y1, b_vec)

        # Compute the linear combinations to obtain v_1 and a_1 (at dof vector level)
        a1 = 1.0 / (beta * dt ** 2) * (y1 - y0) - 1.0 / (beta * dt) * v0 - (1.0 / (2 * beta) - 1.0) * a0
        v1 = gamma / (beta * dt) * (y1 - y0) - (gamma / beta - 1) * v0 - (gamma / (2.0 * beta) - 1.0) * dt * a0

        # Update the dofs for the next time step
        y0.set_local(y1.get_local())
        v0.set_local(v1.get_local())
        a0.set_local(a1.get_local())

        # Update time bar
        drawProgressBar(jt + 1, Nt)

        # Store acceleration, velocity and displacement in a function
        displacement.vector().set_local(y0.get_local())
        velocity.vector().set_local(v0.get_local())
        acceleration.vector().set_local(a0.get_local())

        # Compute the exact solution
        uex.t = dt * (jt + 1)
        solution = interpolate(uex, V)
        solution.rename("uex", "uex")

        # Write values only at some specific time steps (each Nplot time steps values are saved to file)
        Nplot = 1
        if jt % Nplot == 0:
            xdmf_file.write(displacement, dt * (jt + 1))
            xdmf_file.write(velocity, dt * (jt + 1))
            xdmf_file.write(acceleration, dt * (jt + 1))
            xdmf_file.write(solution, dt * (jt + 1))

    # Ending -----------------------------------------------------------------------------------------------------------

    xdmf_file.close()

    if norm(solution) != 0:
        finalError = 100.* errornorm(displacement, solution)/norm(solution)
    else:
        finalError = -1

    print('\n\n Nele = %g' % Nele)
    print(' h = %g' % mesh.hmax())
    print(' dt = %g' % dt)
    print(' Error = %g' % finalError)
    sys.stdout.write("\n")
    return finalError

# ========== UNIT TESTS ================================================================================================
# to run the unit test type:
#           py.test-3 -s -v 2_rigid-transparent_fluid.py

def test_order_h_dt():

    # ---------- PARAMETERS --------------------------------------------------------------------------------------------

    # Time interval
    T_init = 0.0  # initial time
    T_final = 0.5 / vel_fluid
    tol = 0.1 # error tolerance (%)

    # ---------- SETUP -------------------------------------------------------------------------------------------------

    # Open CSV file to store errors
    csvFile = open('TestResultsError/%s_errorLog.csv' % (probIdStr), 'w', newline='')
    csvWriter = csv.writer(csvFile)
    csvWriter.writerow(["h", "dt", "error"])

    errorLogDt = []; dtLogDt = []
    errorLogH = []; hLogH = []
    errorLogComb = []; hLogComb = []; dtLogComb = []

    # ---------- COMPUTATION -------------------------------------------------------------------------------------------

    # Fixing h - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    Nele = 200
    h = (L1 - L0) / Nele
    for dt in [5.83E-05, 2.92E-05, 1.46E-05, 7.29E-06]:
        error = compute(Nele, dt, T_init, T_final, initCondId=6)

        csvWriter.writerow([h, dt, error])
        errorLogDt = np.append(errorLogDt, error)
        dtLogDt = np.append(dtLogDt, dt)

    # Fixing dt - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    dt = 2.73E-05
    for Nele in [50, 100, 200, 400]:
        h = (L1 - L0) / Nele
        error = compute(Nele, dt, T_init, T_final, initCondId=6)

        csvWriter.writerow([h, dt, error])
        errorLogH = np.append(errorLogH, error)
        hLogH = np.append(hLogH, h)

    # Combined - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    for Nele in [50, 100, 200, 400]:
        h = (L1 - L0) / Nele
        dt = h / vel_fluid
        error = compute(Nele, dt, T_init, T_final, initCondId=6)

        csvWriter.writerow([h, dt, error])
        errorLogComb = np.append(errorLogComb, error)
        hLogComb = np.append(hLogComb, h)
        dtLogComb = np.append(dtLogComb, dt)

    csvFile.close() # Close CSV file


    # ---------- GRAPHS ------------------------------------------------------------------------------------------------

    fig = plt.figure(figsize=(8, 8))
    fig.subplots_adjust(wspace=0, hspace=0.5)
    textAlignX = 0.05;
    textAlignY = 0.75;
    fontSize = 12

    # LINEAR DATA - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

    # Error v. dt
    slope, intercept, r_value, p_value, std_err = stats.linregress(dtLogDt, errorLogDt)
    ax = fig.add_subplot(2, 1, 1)
    ax.plot(dtLogDt, errorLogDt, marker = "o")
    ax.plot(dtLogDt, intercept + slope * dtLogDt, color = 'red')
    ax.set(xlabel='$\Delta t$', ylabel='$L^2$-relative error (%)', title='$\Delta x = 5E-03$')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta t$' %(intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    # Error v. h
    slope, intercept, r_value, p_value, std_err = stats.linregress(hLogH, errorLogH)
    ax = fig.add_subplot(2, 1, 2)
    ax.plot(hLogH, errorLogH, marker = "o")
    ax.plot(hLogH, intercept + slope * hLogH, color = 'red')
    ax.set(xlabel='$\Delta x$', ylabel='$L^2$-relative error (%)', title='$\Delta t = 2.73E-05$')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta x$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    # Save
    fig.savefig('TestResultsError/%s_errorLinear.png' % probIdStr)
    fig.clf()

    # COMBINED LINEAR DATA - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    fig = plt.figure(figsize=(8, 12))
    fig.subplots_adjust(wspace=0, hspace=0.5)
    textAlignX = 0.05;
    textAlignY = 0.75;
    fontSize = 12

    # Error v. dt
    slope, intercept, r_value, p_value, std_err = stats.linregress(dtLogComb, errorLogComb)
    ax = fig.add_subplot(3, 1, 1)
    ax.plot(dtLogComb, errorLogComb, marker="o")
    ax.plot(dtLogComb, intercept + slope * dtLogComb, color='red')
    ax.set(xlabel='$\Delta t$', ylabel='$L^2$-relative error (%)', title='Projection on $\Delta t$ ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta t$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    # Error v. h
    slope, intercept, r_value, p_value, std_err = stats.linregress(hLogComb, errorLogComb)
    ax = fig.add_subplot(3, 1, 2)
    ax.plot(hLogComb, errorLogComb, marker="o")
    ax.plot(hLogComb, intercept + slope * hLogComb, color='red')
    ax.set(xlabel='$\Delta x$', ylabel='$L^2$-relative error (%)', title='Projection on $\Delta x$ ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta x$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    # Error v. CFL
    cflLogComb = np.sqrt(hLogComb ** 2 + dtLogComb ** 2)
    slope, intercept, r_value, p_value, std_err = stats.linregress(cflLogComb, errorLogComb)
    ax = fig.add_subplot(3, 1, 3)
    ax.plot(cflLogComb, errorLogComb, marker="o")
    ax.plot(cflLogComb, intercept + slope * cflLogComb, color='red')
    ax.set(xlabel='CFL line', ylabel='$L^2$-relative error (%)', title='Projection on CFL line ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*x' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    # Save
    fig.savefig('TestResultsError/%s_errorCombLin.png' % probIdStr)
    fig.clf()


    # LOG DATA - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    fig = plt.figure(figsize=(8, 8))
    fig.subplots_adjust(wspace=0, hspace=0.5)
    textAlignX = 0.05;
    textAlignY = 0.75;
    fontSize = 12

    # Error v. dt
    slope, intercept, r_value, p_value, std_err = stats.linregress(np.log(dtLogDt), np.log(errorLogDt))
    ax = fig.add_subplot(2, 1, 1)
    ax.loglog(dtLogDt, errorLogDt, marker="o")
    ax.loglog(dtLogDt, np.exp(intercept + slope * np.log(dtLogDt)), color='red')
    ax.set(xlabel='$\Delta t$', ylabel='$L^2$-relative error (%)', title='$\Delta x = 5E-03$')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta t$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    msg = '\n Slope on log(dt) = %g' % slope
    print(msg)
    #assert abs(slopeDt - 2) < tol, msg

    # Error v. h
    slope, intercept, r_value, p_value, std_err = stats.linregress(np.log(hLogH), np.log(errorLogH))
    ax = fig.add_subplot(2, 1, 2)
    ax.loglog(hLogH, errorLogH, marker="o")
    ax.loglog(hLogH, np.exp(intercept + slope * np.log(hLogH)), color='red')
    ax.set(xlabel='$\Delta x$', ylabel='$L^2$-relative error (%)', title='$\Delta t = 2.73E-05$')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta x$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    msg = '\n Slope on log(h) = %g' % slope
    print(msg)
    #assert abs(slopeH - 2) < tol, msg

    # Save
    fig.savefig('TestResultsError/%s_errorLog.png' % probIdStr)
    fig.clf()

    # COMBINED LOG DATA - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    fig = plt.figure(figsize=(8, 12))
    fig.subplots_adjust(wspace=0, hspace=0.5)
    textAlignX = 0.05;
    textAlignY = 0.75;
    fontSize = 12

    # Error v. dt
    slope, intercept, r_value, p_value, std_err = stats.linregress(np.log(dtLogComb), np.log(errorLogComb))
    ax = fig.add_subplot(3, 1, 1)
    ax.loglog(dtLogComb, errorLogComb, marker="o")
    ax.loglog(dtLogComb, np.exp(intercept + slope * np.log(dtLogComb)), color='red')
    ax.set(xlabel='$\Delta t$', ylabel='$L^2$-relative error (%)', title='Projection on $\Delta t$ ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta t$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    msg = '\n Slope on log(dt) (combined) = %g' % slope
    print(msg)
    #assert abs(slope - 2) < tol, msg

    # Error v. h
    slope, intercept, r_value, p_value, std_err = stats.linregress(np.log(hLogComb), np.log(errorLogComb))
    ax = fig.add_subplot(3, 1, 2)
    ax.loglog(hLogComb, errorLogComb, marker="o")
    ax.loglog(hLogComb, np.exp(intercept + slope * np.log(hLogComb)), color='red')
    ax.set(xlabel='$\Delta x$', ylabel='$L^2$-relative error (%)', title='Projection on $\Delta x$ ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*$\Delta x$' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    msg = '\n Slope on log(h) (combined) = %g' % slope
    print(msg)
    #assert abs(slope - 2) < tol, msg

    # Error v. CFL
    slope, intercept, r_value, p_value, std_err = stats.linregress(np.log(cflLogComb), np.log(errorLogComb))
    ax = fig.add_subplot(3, 1, 3)
    ax.loglog(cflLogComb, errorLogComb, marker="o")
    ax.loglog(cflLogComb, np.exp(intercept + slope * np.log(cflLogComb)), color='red')
    ax.set(xlabel='CFL line', ylabel='$L^2$-relative error (%)', title='Projection on CFL line ($CFL = 1$)')
    ax.text(textAlignX, textAlignY, 'error=%g+%g*x' % (intercept, slope), transform=ax.transAxes, fontsize=fontSize)

    msg = '\n Slope on log(CFL line) (combined) = %g' % slope
    print(msg)
    #assert abs(slope - 2) < tol, msg

    # Save
    fig.savefig('TestResultsError/%s_errorCombLog.png' % probIdStr)
    fig.clf()

# ========== MAIN ======================================================================================================

if __name__ == '__main__':

    # ========== PARAMETERS ==========

    # Time interval
    T_init = 0.0  # initial time
    T_final = 1.75 / vel_fluid  # final time (time for only two reflections)

    Nele = 200
    h = (L1-L0) / Nele
    dt = h / vel_fluid

    # ========== COMPUTATION ==========
    #print('Inital Condition 1 error: %g' % compute(Nele, dt, T_init, T_final, initCondId=3))
    #print('Inital Condition 2 error: %g' % compute(Nele, dt, T_init, T_final))

    print('Test error: %g' % compute(Nele, dt, T_init, T_final, initCondId=6, savePrefix='_test_'))