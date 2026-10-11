"""Optional demo preferences: GraspGenX's candidates re-ranked by the xlerobot-demo project (GraspMolmo, DenseMatcher,
HOGraspFlow), run as a subprocess in its own environment. The existing task holds gpu_lock around this bridge."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import os
import subprocess
import tempfile

import numpy as np
from loguru import logger

from config.settings import use_utf8_output

# Baseline is GraspGenX's own order; the others re-rank its SO-101 candidates (doc in xlerobot-demo/doc/research.md).
MODES = ('baseline', 'molmo', 'densematcher', 'hog', 'molmo+densematcher', 'molmo+densematcher+hog')


class DemoPreferences:
    def __init__(self):
        root = Path(__file__).resolve().parents[2] / 'xlerobot-demo'
        self.root = Path(os.getenv('XLEROBOT_DEMO_DIR', str(root)))
        self.python = Path(os.getenv('DEMO_PYTHON', str(self.root / '.venv/bin/python')))
        self.options = {'mode': 'baseline', 'task': 'Pick up the bottle'}
        self.report = {'used': [], 'status': 'baseline'}
        self.scene = None
        self.directory = None

    def configure(self, options):
        if options['mode'] not in MODES or not options['task'].strip():
            raise ValueError('invalid algorithm mode/task')
        for token in ('densematcher','hog'):
            if token in options['mode'].split('+'):
                name = options.get(token+'_prior')
                if not name or not Path(name).is_file():
                    raise ValueError(f'{token}: current-scene registered prior file required')
        self.options = dict(options)
        self.scene = None
        self.report = {'used': [], 'status': 'configured', 'mode': options['mode']}

    def worker(self, request, name):
        folder = Path(self.directory.name)
        path, output = folder / (name+'.request.json'), folder / (name+'.json')
        path.write_text(json.dumps(request), encoding='utf-8')
        with (folder / (name+'.log')).open('w') as log:
            subprocess.run([str(self.python), str(self.root/'app/planning_prior.py'), str(path), str(output)],
                           stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
        return json.loads(output.read_text())

    def prepare(self, scene, camera_from_base):
        if self.options['mode'] == 'baseline':
            self.report = {'used': ['GraspGenX'], 'status': 'baseline'}
            return
        if self.scene is scene:
            return
        if self.directory:
            self.directory.cleanup()
        output = self.root/'output/integration'
        output.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='scene-', dir=output)
        frame_path = Path(self.directory.name)/'frame.npz'
        np.savez_compressed(frame_path,
                            **{key: scene.target.frame[key] for key in ("color", "depth", "K", "depth_scale")},
                            camera_from_base=camera_from_base, points_base=scene.points)
        self.report = {'used': [], 'status': 'inferring', 'mode': self.options['mode']}
        prior = self.worker(dict(self.options, operation='prepare', frame=str(frame_path)), 'prior')
        self.scene = scene
        self.report = {'used': ['GraspGenX'] + prior['source'].split('+'), 'status': 'prior_ready', 'prior': prior,
                       'observation_image': True}

    def order(self, grasps, gripper):
        if self.options['mode'] == 'baseline' or not grasps:
            return grasps
        folder = Path(self.directory.name)
        np.savez_compressed(folder/'candidates.npz', poses=np.array([g.pose for g in grasps]),
                            scores=[g.score for g in grasps])
        result = self.worker(dict(operation='rank', frame=str(folder/'frame.npz'), prior=str(folder/'prior.json'),
                                 candidates=str(folder/'candidates.npz'), gripper=gripper), 'rank')
        for position, row in enumerate(result['ranked']):
            row['candidate'] = grasps[row['index']].index
            row['baseline_rank'] = row['index'] + 1
            row['semantic_rank'] = position + 1
        self.report.update(status=result['status'], candidates=len(grasps), ranked=result['ranked'])
        # Reorder actual IK poses only. Never create, turn, or move a grasp from a human pose.
        return [grasps[row['index']] for row in result['ranked']]


def demo_demo_preferences() -> None:
    """Offline: the modes, and the options after choosing the baseline (the demo project is not run)."""
    preferences = DemoPreferences()
    preferences.configure({"mode": "baseline", "task": "Pick up the bottle"})
    logger.info("{} -> {} ({})", MODES, preferences.options, preferences.report)


def main() -> None:
    use_utf8_output()
    demo_demo_preferences()


if __name__ == "__main__":
    main()
