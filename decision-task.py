# coding: utf8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


import os.path
from decisionlib import DecisionTask


def main():
    decision = DecisionTask(
        project_name="Servo",  # Used in task names
        route_prefix="project.servo.servo",
        worker_type="servo-docker-worker",
    )

    # FIXME: remove this before merging in servo/servo
    os.environ["GIT_URL"] = "https://github.com/SimonSapin/servo"
    os.environ["GIT_REF"] = "refs/heads/taskcluster-experiments-20180920"
    os.environ["GIT_SHA"] = "a6dbfdd29f9b3f0ce0c13adc79fad99538a9a44b"
    decision.docker_image_cache_expiry = "1 week"
    decision.route_prefix = "project.servo.servo-taskcluster-experiments"
    # ~


    # https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/caches
    cache_scopes = [
        "docker-worker:cache:cargo-*",
    ]
    build_caches = {
        "cargo-registry-cache": "/root/.cargo/registry",
        "cargo-git-cache": "/root/.cargo/git",
        "cargo-rustup": "/root/.rustup",
        "cargo-sccache": "/root/.cache/sccache",
    }
    build_artifacts_expiry = "1 week"
    build_env = {
        "RUST_BACKTRACE": "1",
        "RUSTFLAGS": "-Dwarnings",
        "CARGO_INCREMENTAL": "0",
        "SCCACHE_IDLE_TIMEOUT": "1200",
        "CCACHE": "sccache",
        "RUSTC_WRAPPER": "sccache",
    }
    build_kwargs = {
        "max_run_time_minutes": 60,
        "dockerfile": "build-x86_64-linux.dockerfile",
        "env": build_env,
        "scopes": cache_scopes,
        "cache": build_caches,
    }

    decision.create_task(
        task_name="Linux x86_64: tidy + dev build + unit tests",
        script="""
            ./mach test-tidy --no-progress --all
            ./mach build --dev
            ./mach test-unit
            ./mach package --dev
            ./mach test-tidy --no-progress --self-test
            python ./etc/memory_reports_over_time.py --test
            ./etc/ci/lockfile_changed.sh
            ./etc/ci/check_no_panic.sh
        """,
        **build_kwargs
    )

    release_build_task = decision.find_or_create_task(
        route_bucket="build.linux_x86-64_release",
        route_key=os.environ["GIT_SHA"],
        route_expiry=build_artifacts_expiry,

        task_name="Linux x86_64: release build",
        script="""
            ./mach build --release --with-debug-assertions -p servo
            ./etc/ci/lockfile_changed.sh
            tar -czf /target.tar.gz \
                target/release/servo \
                target/release/build/osmesa-src-*/output \
                target/release/build/osmesa-src-*/out/lib/gallium
        """,
        artifacts=[
            "/target.tar.gz",
        ],
        **build_kwargs
    )

    def create_run_task(*, script, env=None, **kwargs):
        fetch_build = """
            curl \
                "https://queue.taskcluster.net/v1/task/${BUILD_TASK_ID}/artifacts/public/target.tar.gz" \
                --retry 5 \
                --connect-timeout 10 \
                --location \
                --fail \
                | tar -xz
        """
        decision.create_task(
            script=fetch_build + script,
            env=dict(**env or {}, BUILD_TASK_ID=release_build_task),
            dependencies=[release_build_task],
            max_run_time_minutes=60,
            dockerfile="run-x86_64-linux.dockerfile",
            **kwargs
        )

    total_chunks = 4
    for i in range(total_chunks):
        chunk = i + 1
        create_run_task(
            task_name="Linux x86_64: WPT chunk %s / %s" % (chunk, total_chunks),
            script="""
                ./mach test-wpt \
                    --release \
                    --processes 24 \
                    --total-chunks "$TOTAL_CHUNKS" \
                    --this-chunk "$THIS_CHUNK" \
                    --log-raw test-wpt.log \
                    --log-errorsummary wpt-errorsummary.log \
                    --always-succeed
                ./mach filter-intermittents\
                    wpt-errorsummary.log \
                    --log-intermittents intermittents.log \
                    --log-filteredsummary filtered-wpt-errorsummary.log \
                    --tracker-api default \
                    --reporter-api default
            """,
            env={
                "TOTAL_CHUNKS": total_chunks,
                "THIS_CHUNK": chunk,
            },
        )

    create_run_task(
        task_name="Linux x86_64: WPT extra",
        script="""
            ./mach test-wpt-failure
            ./mach test-wpt --release --binary-arg=--multiprocess --processes 24 \
                --log-raw test-wpt-mp.log \
                --log-errorsummary wpt-mp-errorsummary.log \
                eventsource
        """,
    )


if __name__ == "__main__":
    os.chdir(os.path.join(".", os.path.dirname(__file__)))
    main()
