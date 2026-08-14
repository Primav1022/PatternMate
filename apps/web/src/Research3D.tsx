import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { CompositionRecipe, TryonDescriptor } from './PatternPreview';
import { useLanguage } from './Language';

type FitMetric = { requested?: number; fitted?: number; error?: number } | number | null;
type Job = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';
  stage?: 'queued' | 'mesh' | 'sewing' | 'collision' | 'drape' | 'export' | 'completed' | 'failed';
  progress: number;
  result_url?: string;
  error?: string;
  avatar_hash?: string;
  metadata?: { fit_metrics_cm?: Record<string, FitMetric>; simulation_ready?: boolean; solver?: string };
};

function ModelCanvas({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const element = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#f5efe6');
    const camera = new THREE.PerspectiveCamera(34, 1, .01, 100);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    element.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight('#fff8ed', '#70534a', 2.1));
    const key = new THREE.DirectionalLight('#ffffff', 2.5);
    key.position.set(3, 4, 4);
    scene.add(key);
    let loaded: THREE.Object3D | null = null;
    new GLTFLoader().load(url, (gltf) => {
      loaded = gltf.scene;
      gltf.scene.traverse((object) => {
        if (object instanceof THREE.Mesh) object.geometry.computeVertexNormals();
      });
      scene.add(gltf.scene);
      const box = new THREE.Box3().setFromObject(gltf.scene);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      controls.target.copy(center);
      camera.position.set(center.x, center.y, center.z + Math.max(size.y, size.x) * 1.85);
      controls.update();
    });
    const resize = () => {
      const width = Math.max(element.clientWidth, 1);
      const height = Math.max(element.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    let frame = 0;
    const render = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(render); };
    render();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      if (loaded) scene.remove(loaded);
      element.replaceChildren();
    };
  }, [url]);
  return <div className="research-model-canvas" ref={host} />;
}

async function pollJob(base: string, jobId: string, signal: AbortSignal, update: (job: Job) => void): Promise<Job> {
  while (!signal.aborted) {
    const response = await fetch(`${base}/research/jobs/${jobId}`, { signal });
    if (!response.ok) throw new Error('3D 任务状态不可用');
    const job: Job = await response.json();
    update(job);
    if (['completed', 'failed', 'cancelled'].includes(job.status)) return job;
    await new Promise((resolve) => window.setTimeout(resolve, 350));
  }
  throw new DOMException('Aborted', 'AbortError');
}

const STAGES: Record<string, [string, string]> = {
  queued: ['等待生成', 'Waiting to start'],
  mesh: ['准备服装', 'Preparing garment'],
  sewing: ['整理服装结构', 'Preparing garment structure'],
  collision: ['贴合人体', 'Fitting to body'],
  drape: ['模拟自然垂坠', 'Creating natural drape'],
  export: ['生成预览', 'Preparing preview'],
  completed: ['完成', 'Completed'],
};

export function Research3D({ measurements, sex, recipe, composition, mode, onReady, onUnavailable }: {
  measurements: Record<string, string>;
  sex: 'female' | 'male_general';
  recipe?: CompositionRecipe;
  composition?: TryonDescriptor;
  mode: 'avatar' | 'tryon';
  onReady?: (job: Job) => void;
  onUnavailable?: (message: string) => void;
}) {
  const { t } = useLanguage();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [modelUrl, setModelUrl] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const serialized = JSON.stringify({ measurements, sex, recipe, composition, mode, retry });

  useEffect(() => {
    const controller = new AbortController();
    const base = import.meta.env.VITE_TRYON_BASE_URL || '/tryon';
    let currentJob = '';
    const run = async () => {
      try {
        setError('');
        setModelUrl('');
        if (mode === 'tryon') {
          const validation = composition?.validation;
          if (!recipe || composition?.version !== 'patternmate.tryon.v2' || !validation?.tryon_ready) {
            throw new Error(t('当前服装组合暂时无法生成 3D 效果，请返回调整后重试。', 'This garment combination cannot be previewed in 3D yet. Adjust it and try again.'));
          }
        }
        const healthResponse = await fetch(`${base}/research/health`, { signal: controller.signal });
        const health = await healthResponse.json();
        if (!healthResponse.ok || !health.enabled || (mode === 'tryon' && !health.cloth_solver_available)) {
          throw new Error(t('3D 试穿暂时不可用，请稍后重试。', '3D try-on is temporarily unavailable. Please try again later.'));
        }
        setAvailable(true);
        const numericMeasurements = Object.fromEntries(Object.entries(measurements).map(([key, value]) => [key, Number(value)]));
        const avatarResponse = await fetch(`${base}/research/avatar/jobs`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ sex, measurements_cm: numericMeasurements }), signal: controller.signal,
        });
        if (!avatarResponse.ok) throw new Error(t('暂时无法生成人体预览，请重试。', 'Unable to create the body preview. Please try again.'));
        const avatar: Job = await avatarResponse.json();
        currentJob = avatar.job_id;
        setJob(avatar);
        const avatarDone = await pollJob(base, avatar.job_id, controller.signal, setJob);
        if (avatarDone.status !== 'completed' || !avatarDone.result_url) throw new Error(t('人体预览生成失败，请重试。', 'Body preview generation failed. Please try again.'));
        if (mode === 'avatar' || !recipe) {
          setModelUrl(`${base}${avatarDone.result_url}`);
          onReady?.(avatarDone);
          return;
        }
        const payload = {
          avatar_id: avatar.avatar_hash || avatar.job_id,
          recipe_hash: composition?.recipe_hash || JSON.stringify(recipe),
          family: recipe.family,
          sex,
          measurements_cm: numericMeasurements,
          recipe,
          composition_descriptor: composition || {},
          material: { id: recipe.material_id, color: recipe.fabric_color },
        };
        for (const quality of ['draft'] as const) {
          const response = await fetch(`${base}/research/tryon/jobs`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ ...payload, quality }), signal: controller.signal,
          });
          const body = await response.json().catch(() => null);
          if (!response.ok) throw new Error(t('暂时无法创建 3D 试穿，请重试。', 'Unable to create the 3D try-on. Please try again.'));
          currentJob = body.job_id;
          setJob(body);
          const done = await pollJob(base, body.job_id, controller.signal, setJob);
          if (done.status !== 'completed' || !done.result_url) throw new Error(t('3D 试穿生成失败，请重试或调整当前搭配。', '3D try-on failed. Please retry or adjust the current design.'));
          setModelUrl(`${base}${done.result_url}`);
          onReady?.(done);
        }
      } catch (caught) {
        if (controller.signal.aborted) return;
        const message = caught instanceof Error ? caught.message : '3D 服务不可用';
        setModelUrl('');
        setAvailable(false);
        setError(message);
        onUnavailable?.(message);
      }
    };
    const timer = window.setTimeout(run, mode === 'avatar' ? 0 : 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (currentJob) void fetch(`${base}/research/jobs/${currentJob}`, { method: 'DELETE' }).catch(() => undefined);
    };
  }, [serialized]);

  if (error) return <div className="research-unavailable"><strong>{t('3D 仿真失败', '3D simulation failed')}</strong><span>{error}</span><button type="button" onClick={() => setRetry((value) => value + 1)}>{t('重试', 'Retry')}</button></div>;
  const stage = STAGES[job?.stage || 'queued'] || STAGES.queued;
  return <div className="research-viewer">
    {modelUrl ? <ModelCanvas url={modelUrl} /> : <div className="research-loading"><span className="compose-spinner" /><strong>{mode === 'avatar' ? t('正在生成人体预览', 'Preparing body preview') : t(stage[0], stage[1])}</strong><small>{job ? `${job.progress}%` : t('正在准备 3D 预览', 'Preparing 3D preview')}</small></div>}
    {available === null && !job && <span className="sr-only">loading</span>}
  </div>;
}
