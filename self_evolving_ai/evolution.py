import random, time

MUTATION_RATE = 0.15
POPULATION_SIZE = 100

def _create_genome(length):
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz .,!?:;-', k=length))

def _fitness(genome, target):
    return sum(a == b for a, b in zip(genome, target)) / max(1, len(target))

def _mutate(genome):
    gl = list(genome)
    for i in range(len(gl)):
        if random.random() < MUTATION_RATE:
            gl[i] = random.choice('abcdefghijklmnopqrstuvwxyz .,!?:;-')
    return ''.join(gl)

def _crossover(a,b):
    if len(a) <= 1:
        return a
    pt = random.randint(1, len(a)-1)
    return a[:pt] + b[pt:]

def evolve_background(state, shutdown_event):
    target = state.get_target()
    L = len(target)
    population = [_create_genome(L) for _ in range(POPULATION_SIZE)]
    best = population[0]
    state.set_best(best)

    while not shutdown_event.is_set():
        target = state.get_target()
        L = len(target)

        population.sort(key=lambda g: _fitness(g, target), reverse=True)
        if _fitness(population[0], target) > _fitness(best, target):
            best = population[0]
            state.set_best(best)

        survivors = population[:POPULATION_SIZE // 4]
        offspring = []
        while len(offspring) < POPULATION_SIZE - len(survivors):
            offspring.append(_mutate(_crossover(*random.sample(survivors, 2))))
        population = survivors + offspring
        time.sleep(0.3)

def learn_overnight(state, shutdown_event):
    import time
    print("Machine Spirit beginning overnight learning...")
    # Simulate fetching data (e.g., from a local file or web)
    with open("/home/aaron/self-learning-ai/knowledge.txt", "r") as f:
        knowledge = f.read().splitlines()
    for line in knowledge:
        state.append_to_target(line)  # Update target phrase
        time.sleep(0.1)  # Simulate processing time
    print("Overnight learning complete.")
