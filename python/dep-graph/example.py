"""Runnable demo for depgraph: schedule a release pipeline, then break it.

    python example.py
"""

from depgraph import CycleError, Graph

PIPELINE = {
    "checkout": [],
    "install-deps": ["checkout"],
    "lint": ["install-deps"],
    "unit-tests": ["install-deps"],
    "build-image": ["install-deps"],
    "integration-tests": ["build-image"],
    "security-scan": ["build-image"],
    "stage-deploy": ["unit-tests", "integration-tests", "security-scan", "lint"],
    "smoke-tests": ["stage-deploy"],
    "prod-deploy": ["smoke-tests"],
}


def show_layers(graph):
    for depth, layer in enumerate(graph.layers()):
        joined = ", ".join(layer)
        print(f"  round {depth + 1}: {joined}")
    print()


def main():
    graph = Graph.from_mapping(PIPELINE)
    graph.validate()
    print(f"{graph}\n")

    print("Sequential order (deterministic, ties alphabetical):")
    print("  " + " -> ".join(graph.topological_order()) + "\n")

    print("Rounds that can run in parallel:")
    show_layers(graph)

    path = graph.critical_path()
    print(f"Critical path ({len(path)} rounds, the floor for wall-clock time):")
    print("  " + " -> ".join(path) + "\n")

    print("If `install-deps` changes, re-run:")
    print("  " + ", ".join(graph.impacted_by("install-deps")) + "\n")

    print("Blast radius of a late-stage failure is small:")
    print("  prod-deploy impacts: " + (", ".join(graph.impacted_by("prod-deploy")) or "nothing"))
    print()

    # Slice the graph down to just the container work.
    container_work = graph.subgraph(
        {"build-image", "integration-tests", "security-scan", "install-deps"}
    )
    print(f"Subgraph for the image pipeline: {container_work}")
    print("  rounds: " + str(container_work.layers()) + "\n")

    # Now introduce a real mistake: someone declares that installing
    # dependencies needs a prod deploy to have happened first, which loops the
    # whole pipeline back on itself.
    broken = Graph.from_mapping(PIPELINE)
    broken.add_dependency("install-deps", "prod-deploy")
    try:
        broken.topological_order()
    except CycleError as error:
        print(f"rejected as expected: {error}")
        print("  offending path: " + " -> ".join(error.cycle))
        print(f"  that path has {len(error.cycle) - 1} hops")
    print(f"\nThe healthy graph still has {len(graph)} nodes and no cycle: "
          f"{graph.find_cycle() is None}")


if __name__ == "__main__":
    main()
