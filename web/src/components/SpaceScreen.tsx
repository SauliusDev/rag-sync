import { RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { SpaceChunk, SpaceResponse } from '../api';
import { ScreenHeader } from './ui/ScreenHeader';
import { SectionBlock } from './ui/SectionBlock';

type SpaceScreenProps = {
  space: SpaceResponse | null;
  loading: boolean;
  error: string;
};

const palette = ['#2563eb', '#16a34a', '#c2410c', '#7c3aed', '#0f766e', '#be123c'];

function datasetColor(name: string, index: number) {
  return palette[index % palette.length];
}

export function SpaceScreen({ space, loading, error }: SpaceScreenProps) {
  const [rotation, setRotation] = useState({ x: 58, y: -34 });
  const [zoom, setZoom] = useState(1);
  const [selectedId, setSelectedId] = useState('');

  const datasetColors = useMemo(() => {
    const entries: Array<[string, string]> = (space?.datasets ?? []).map((dataset, index) => [
      dataset.name,
      datasetColor(dataset.name, index),
    ]);
    return new Map(entries);
  }, [space?.datasets]);

  const selectedChunk =
    space?.chunks.find((chunk) => chunk.id === selectedId) ?? space?.chunks[0] ?? null;

  return (
    <div className="space-screen">
      <ScreenHeader
        id="space-screen-title"
        title="Space"
        subtitle="Rotate through projected RAGFlow chunks and inspect the source text behind each point."
      />
      <section className="screen-content" aria-labelledby="space-screen-title">
        {error ? (
          <p className="dataset-banner" role="alert">
            {error}
          </p>
        ) : null}
        {loading && !space ? (
          <p className="muted" role="status" aria-live="polite">
            Fetching chunk space from RAGFlow.
          </p>
        ) : null}
        {space ? (
          <SpaceExplorer
            datasetColors={datasetColors}
            rotation={rotation}
            selectedChunk={selectedChunk}
            selectedId={selectedId}
            setRotation={setRotation}
            setSelectedId={setSelectedId}
            setZoom={setZoom}
            space={space}
            zoom={zoom}
          />
        ) : null}
      </section>
    </div>
  );
}

function SpaceExplorer({
  datasetColors,
  rotation,
  selectedChunk,
  selectedId,
  setRotation,
  setSelectedId,
  setZoom,
  space,
  zoom,
}: {
  datasetColors: Map<string, string>;
  rotation: { x: number; y: number };
  selectedChunk: SpaceChunk | null;
  selectedId: string;
  setRotation: (value: { x: number; y: number }) => void;
  setSelectedId: (value: string) => void;
  setZoom: (value: (previous: number) => number) => void;
  space: SpaceResponse;
  zoom: number;
}) {
  const fetchIssues = useMemo(() => {
    const cache = space.cache as ({ stale?: boolean; status?: string } | undefined);
    const onlyCredentialErrors =
      space.errors.length > 0 &&
      space.errors.every((item) => item.message.includes('RAGFLOW_API_KEY') || item.message.includes('RAGFLOW_MCP_HOST_API_KEY'));
    if (space.chunks.length > 0 && onlyCredentialErrors && (cache?.stale || cache?.status === 'hit')) {
      return [];
    }
    const seen = new Set<string>();
    return space.errors.filter((item) => {
      const key = `${item.document_name || ''}:${item.message}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }, [space.errors]);

  return (
    <div className="space-layout">
      <div className="space-main">
        <div className="space-toolbar" aria-label="Space controls">
          <button
            className="icon-button"
            type="button"
            aria-label="Rotate left"
            onClick={() => setRotation({ ...rotation, y: rotation.y - 14 })}
          >
            <RotateCcw size={17} aria-hidden="true" />
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label="Zoom out"
            onClick={() => setZoom((value) => Math.max(0.7, value - 0.12))}
          >
            <ZoomOut size={17} aria-hidden="true" />
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label="Zoom in"
            onClick={() => setZoom((value) => Math.min(1.8, value + 0.12))}
          >
            <ZoomIn size={17} aria-hidden="true" />
          </button>
        </div>
        <OrientationCompass rotation={rotation} />
        <div
          className="space-stage"
          role="application"
          aria-label={`${space.summary.chunks} projected RAGFlow chunks`}
        >
          <SpaceCanvas
            chunks={space.chunks}
            datasetColors={datasetColors}
            rotation={rotation}
            selectedId={selectedChunk?.id ?? ''}
            setRotation={setRotation}
            setSelectedId={setSelectedId}
            setZoom={setZoom}
            zoom={zoom}
          />
        </div>
      </div>
      <aside className="space-sidebar" aria-label="Space detail">
        <SectionBlock title="Overview" id="space-overview-title">
          <dl className="settings-list">
            <div>
              <dt>Datasets</dt>
              <dd>{space.summary.datasets}</dd>
            </div>
            <div>
              <dt>Documents</dt>
              <dd>{space.summary.documents}</dd>
            </div>
            <div>
              <dt>Chunks</dt>
              <dd>{space.summary.chunks}</dd>
            </div>
          </dl>
        </SectionBlock>
        <SectionBlock title="Datasets" id="space-datasets-title">
          <div className="space-legend">
            {space.datasets.map((dataset) => (
              <span className="space-legend-item" key={dataset.id}>
                <span
                  className="space-legend-dot"
                  style={{ background: datasetColors.get(dataset.name) ?? palette[0] }}
                />
                {dataset.name}
              </span>
            ))}
          </div>
        </SectionBlock>
        <SectionBlock title="Chunk" id="space-chunk-title">
          {selectedChunk ? (
            <div className="space-chunk-detail">
              <strong>{selectedChunk.document_name}</strong>
              <p>{selectedChunk.content_preview}</p>
              <dl className="settings-list">
                <div>
                  <dt>Dataset</dt>
                  <dd>{selectedChunk.dataset_name}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{selectedChunk.source_path || 'unknown'}</dd>
                </div>
                <div>
                  <dt>Keywords</dt>
                  <dd>{selectedChunk.keywords.join(', ') || 'none'}</dd>
                </div>
              </dl>
            </div>
          ) : (
            <p className="dataset-empty">No chunks available.</p>
          )}
        </SectionBlock>
        {fetchIssues.length > 0 ? (
          <SectionBlock title="Fetch issues" id="space-errors-title">
            <div className="space-errors">
              {fetchIssues.map((item) => (
                <p key={`${item.document_name}-${item.message}`}>
                  {item.document_name ? `${item.document_name}: ` : ''}
                  {item.message}
                </p>
              ))}
            </div>
          </SectionBlock>
        ) : null}
      </aside>
    </div>
  );
}

function OrientationCompass({ rotation }: { rotation: { x: number; y: number } }) {
  const axes = [
    { key: 'x', label: 'X', vector: { x: 1, y: 0, z: 0 } },
    { key: 'y', label: 'Y', vector: { x: 0, y: -1, z: 0 } },
    { key: 'z', label: 'Z', vector: { x: 0, y: 0, z: 1 } },
  ];

  return (
    <div className="space-orientation" aria-label="3D space orientation">
      <svg viewBox="0 0 96 96" role="img" aria-hidden="true">
        <circle className="space-orientation-ring" cx="48" cy="48" r="29" />
        {axes.map((axis) => {
          const rotated = rotatePoint(axis.vector, rotation);
          const endX = 48 + rotated.x * 25;
          const endY = 48 + rotated.y * 25;
          const labelX = 48 + rotated.x * 34;
          const labelY = 48 + rotated.y * 34;
          return (
            <g data-axis={axis.key} key={axis.key}>
              <line
                className={`space-orientation-axis space-orientation-axis-${axis.key}`}
                x1="48"
                y1="48"
                x2={endX}
                y2={endY}
              />
              <circle
                className={`space-orientation-dot space-orientation-axis-${axis.key}`}
                cx={endX}
                cy={endY}
                r={Math.max(2.5, 3.4 + rotated.z)}
              />
              <text className="space-orientation-label" x={labelX} y={labelY}>
                {axis.label}
              </text>
            </g>
          );
        })}
        <circle className="space-orientation-center" cx="48" cy="48" r="3" />
      </svg>
    </div>
  );
}

type ScreenPoint = {
  chunk: SpaceChunk;
  color: string;
  depth: number;
  radius: number;
  screenX: number;
  screenY: number;
};

function rotatePoint(
  point: { x: number; y: number; z: number },
  rotation: { x: number; y: number },
) {
  const xRadians = (rotation.x * Math.PI) / 180;
  const yRadians = (rotation.y * Math.PI) / 180;
  const cosY = Math.cos(yRadians);
  const sinY = Math.sin(yRadians);
  const cosX = Math.cos(xRadians);
  const sinX = Math.sin(xRadians);
  const rotatedX = point.x * cosY - point.z * sinY;
  const rotatedZ = point.x * sinY + point.z * cosY;
  const rotatedY = point.y * cosX - rotatedZ * sinX;
  return {
    x: rotatedX,
    y: rotatedY,
    z: point.y * sinX + rotatedZ * cosX,
  };
}

function SpaceCanvas({
  chunks,
  datasetColors,
  rotation,
  selectedId,
  setRotation,
  setSelectedId,
  setZoom,
  zoom,
}: {
  chunks: SpaceChunk[];
  datasetColors: Map<string, string>;
  rotation: { x: number; y: number };
  selectedId: string;
  setRotation: (value: { x: number; y: number }) => void;
  setSelectedId: (value: string) => void;
  setZoom: (value: (previous: number) => number) => void;
  zoom: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pointsRef = useRef<ScreenPoint[]>([]);
  const dragRef = useRef<{ x: number; y: number; rotation: { x: number; y: number } } | null>(null);
  const coloredChunks = useMemo(
    () =>
      chunks.map((chunk) => ({
        chunk,
        color: datasetColors.get(chunk.dataset_name) ?? palette[0],
      })),
    [chunks, datasetColors],
  );

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) {
      return;
    }
    const rect = parent.getBoundingClientRect();
    const pixelRatio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * pixelRatio));
    canvas.height = Math.max(1, Math.floor(rect.height * pixelRatio));
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);

    const gradient = context.createRadialGradient(
      rect.width * 0.5,
      rect.height * 0.48,
      20,
      rect.width * 0.5,
      rect.height * 0.52,
      Math.max(rect.width, rect.height) * 0.62,
    );
    gradient.addColorStop(0, 'rgba(37, 99, 235, 0.16)');
    gradient.addColorStop(0.45, 'rgba(22, 163, 74, 0.08)');
    gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
    context.fillStyle = gradient;
    context.fillRect(0, 0, rect.width, rect.height);

    const scale = Math.min(rect.width, rect.height) * 0.48 * zoom;
    const nextPoints = coloredChunks.map(({ chunk, color }) => {
      const rotated = rotatePoint(chunk.position, rotation);
      const cameraDistance = 3.2;
      const perspective = cameraDistance / (cameraDistance + rotated.z);
      const screenX = rect.width * 0.5 + rotated.x * scale * perspective;
      const screenY = rect.height * 0.5 + rotated.y * scale * perspective;
      const radius = Math.max(0.45, Math.min(1.9, 0.62 + perspective * 0.42));
      return {
        chunk,
        color,
        depth: rotated.z,
        radius,
        screenX,
        screenY,
      };
    });

    if (nextPoints.length > 0) {
      const center = nextPoints.reduce(
        (accumulator, point) => ({
          x: accumulator.x + point.screenX,
          y: accumulator.y + point.screenY,
        }),
        { x: 0, y: 0 },
      );
      const offsetX = rect.width * 0.5 - center.x / nextPoints.length;
      const offsetY = rect.height * 0.5 - center.y / nextPoints.length;
      for (const point of nextPoints) {
        point.screenX += offsetX;
        point.screenY += offsetY;
      }

      const bounds = nextPoints.reduce(
        (accumulator, point) => ({
          minX: Math.min(accumulator.minX, point.screenX),
          maxX: Math.max(accumulator.maxX, point.screenX),
          minY: Math.min(accumulator.minY, point.screenY),
          maxY: Math.max(accumulator.maxY, point.screenY),
        }),
        { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity },
      );
      const padding = 18;
      const widthFit = (rect.width - padding * 2) / Math.max(1, bounds.maxX - bounds.minX);
      const heightFit = (rect.height - padding * 2) / Math.max(1, bounds.maxY - bounds.minY);
      const fit = Math.min(1, widthFit, heightFit);
      if (fit < 1) {
        for (const point of nextPoints) {
          point.screenX = rect.width * 0.5 + (point.screenX - rect.width * 0.5) * fit;
          point.screenY = rect.height * 0.5 + (point.screenY - rect.height * 0.5) * fit;
        }
      }
    }

    const visiblePoints = nextPoints
      .filter(
        (point) =>
          point.screenX > -12 &&
          point.screenX < rect.width + 12 &&
          point.screenY > -12 &&
          point.screenY < rect.height + 12,
      )
      .sort((a, b) => a.depth - b.depth);
    pointsRef.current = visiblePoints;

    context.save();
    context.globalCompositeOperation = 'lighter';
    context.lineWidth = 0.42;
    for (let index = 0; index < visiblePoints.length - 20; index += 15) {
      const source = visiblePoints[index];
      const target = visiblePoints[index + 20];
      const dx = source.screenX - target.screenX;
      const dy = source.screenY - target.screenY;
      if (dx * dx + dy * dy > 9200) {
        continue;
      }
      context.strokeStyle = 'rgba(96, 165, 250, 0.13)';
      context.beginPath();
      context.moveTo(source.screenX, source.screenY);
      context.lineTo(target.screenX, target.screenY);
      context.stroke();
    }

    for (const point of visiblePoints) {
      const selected = point.chunk.id === selectedId;
      context.fillStyle = selected ? '#ffffff' : point.color;
      context.globalAlpha = selected ? 1 : 0.62;
      context.beginPath();
      context.arc(point.screenX, point.screenY, selected ? 4.5 : point.radius, 0, Math.PI * 2);
      context.fill();
      if (selected) {
        context.strokeStyle = point.color;
        context.lineWidth = 1.4;
        context.globalAlpha = 0.9;
        context.stroke();
      }
    }
    context.restore();
  }, [coloredChunks, rotation, selectedId, zoom]);

  useEffect(() => {
    draw();
    window.addEventListener('resize', draw);
    return () => window.removeEventListener('resize', draw);
  }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      className="space-canvas"
      aria-label="3D projected chunk point cloud"
      role="img"
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const x = event.clientX - bounds.left;
        const y = event.clientY - bounds.top;
        let best: ScreenPoint | null = null;
        let bestDistance = 18 * 18;
        for (const point of pointsRef.current) {
          const dx = point.screenX - x;
          const dy = point.screenY - y;
          const distance = dx * dx + dy * dy;
          if (distance < bestDistance) {
            best = point;
            bestDistance = distance;
          }
        }
        if (best) {
          setSelectedId(best.chunk.id);
        }
      }}
      onMouseDown={(event) => {
        dragRef.current = {
          x: event.clientX,
          y: event.clientY,
          rotation,
        };
      }}
      onMouseMove={(event) => {
        if (!dragRef.current) {
          return;
        }
        const dx = event.clientX - dragRef.current.x;
        const dy = event.clientY - dragRef.current.y;
        setRotation({
          x: Math.max(-82, Math.min(82, dragRef.current.rotation.x + dy * 0.22)),
          y: dragRef.current.rotation.y + dx * 0.22,
        });
      }}
      onMouseUp={() => {
        dragRef.current = null;
      }}
      onMouseLeave={() => {
        dragRef.current = null;
      }}
      onWheel={(event) => {
        event.preventDefault();
        setZoom((value) => Math.max(0.65, Math.min(2.2, value + (event.deltaY > 0 ? -0.08 : 0.08))));
      }}
    />
  );
}
