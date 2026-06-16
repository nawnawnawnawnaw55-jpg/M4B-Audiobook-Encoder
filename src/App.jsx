import React, { useState, useEffect, useRef } from 'react';
import { Download, AlertTriangle, Play, Square, Settings, Image as ImageIcon, GripVertical, Info, UploadCloud } from 'lucide-react';

export default function App() {
  // Application State
  const [files, setFiles] = useState([]);
  const [cover, setCover] = useState(null);
  const [coverPreview, setCoverPreview] = useState(null);
  const [generatedCoverBlob, setGeneratedCoverBlob] = useState(null);
  
  const [meta, setMeta] = useState({ title: '', author: '', narrator: '', genre: '', year: '' });
  const [showMeta, setShowMeta] = useState(false);
  
  const [quality, setQuality] = useState('320k');
  const [chapterMode, setChapterMode] = useState('auto');
  const [showChapterEditor, setShowChapterEditor] = useState(false);
  
  const [status, setStatus] = useState('idle'); // idle, encoding, previewing
  const [progress, setProgress] = useState(0);
  const [eta, setEta] = useState('');
  const [previewPlaying, setPreviewPlaying] = useState(false);
  
  const [isCoiIsolated, setIsCoiIsolated] = useState(true);
  const [totalDuration, setTotalDuration] = useState(0);
  const [ffmpegLoaded, setFfmpegLoaded] = useState(false);
  const [engineError, setEngineError] = useState('');
  
  // Drag and Drop UI States
  const [isDraggingOverApp, setIsDraggingOverApp] = useState(false);
  const [dragOverIndex, setDragOverIndex] = useState(null);

  // Refs
  const ffmpegRef = useRef(null);
  const audioRef = useRef(null);
  const dragItem = useRef(null);
  const dragOverItem = useRef(null);
  const totalAudioDurationRef = useRef(0);
  const fetchFileRef = useRef(null);

  // Dynamic Favicon Setup
  useEffect(() => {
    let link = document.querySelector("link[rel~='icon']");
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    const svgIcon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256" rx="50" fill="%23181818"/><circle cx="128" cy="128" r="100" fill="%23F97300"/><polygon points="105,85 105,171 175,128" fill="%23181818"/></svg>`;
    link.href = `data:image/svg+xml,${svgIcon}`;
  }, []);

  // Initialize FFmpeg with Smart Fallback
  useEffect(() => {
    let isMounted = true;
    
    const loadFFmpeg = async () => {
      const isMT = typeof window !== 'undefined' && window.crossOriginIsolated;
      if (!isMT) setIsCoiIsolated(false);

      const loadScript = (src) => new Promise((resolve, reject) => {
        if (document.querySelector(`script[src="${src}"]`)) return resolve();
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = reject;
        document.body.appendChild(script);
      });

      try {
        await loadScript('https://unpkg.com/@ffmpeg/ffmpeg@0.12.6/dist/umd/ffmpeg.js');
        await loadScript('https://unpkg.com/@ffmpeg/util@0.12.1/dist/umd/index.js');
        
        // Extract the toBlobURL utility along with fetchFile
        const { FFmpeg } = window.FFmpegWASM;
        const { fetchFile, toBlobURL } = window.FFmpegUtil;
        fetchFileRef.current = fetchFile;

        const ffmpeg = new FFmpeg();
        
        ffmpeg.on('log', ({ message }) => {
          const timeMatch = message.match(/time=(\d{2}):(\d{2}):(\d{2}\.\d+)/);
          // Only drive progress bar if we are actively encoding
          if (timeMatch && totalAudioDurationRef.current > 0) {
            const h = parseFloat(timeMatch[1]);
            const m = parseFloat(timeMatch[2]);
            const s = parseFloat(timeMatch[3]);
            const currentSec = (h * 3600) + (m * 60) + s;
            const pct = Math.min((currentSec / totalAudioDurationRef.current) * 100, 99);
            setProgress(pct);
          }
        });

        // Load the multithreaded core if headers are present, otherwise load the standard single-threaded core
        const baseURL = isMT ? 'https://unpkg.com/@ffmpeg/core-mt@0.12.6/dist/umd' : 'https://unpkg.com/@ffmpeg/core@0.12.6/dist/umd';
        
        // FIX: Wrap the CDN URLs in toBlobURL to bypass the browser's Cross-Origin Worker security blocks
        await ffmpeg.load({
          coreURL: await toBlobURL(`${baseURL}/ffmpeg-core.js`, 'text/javascript'),
          wasmURL: await toBlobURL(`${baseURL}/ffmpeg-core.wasm`, 'application/wasm'),
          workerURL: isMT ? await toBlobURL(`${baseURL}/ffmpeg-core.worker.js`, 'text/javascript') : undefined,
          // NEW ADDITION: Fetch the internal FFmpeg class worker as a local Blob
          classWorkerURL: await toBlobURL('https://unpkg.com/@ffmpeg/ffmpeg@0.12.6/dist/umd/814.ffmpeg.js', 'text/javascript'),
        });
        
        if (isMounted) {
          ffmpegRef.current = ffmpeg;
          setFfmpegLoaded(true);
          setEngineError('');
        }
      } catch (err) {
        console.error("FFmpeg failed to load.", err);
        if (isMounted) {
          setEngineError(err.message || "Failed to load processing engine.");
        }
      }
    };
    
    loadFFmpeg();
    
    return () => { isMounted = false; };
  }, []);

  // Utility to get audio duration
  const getAudioDuration = (file) => {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(file);
      const audio = new Audio(url);
      audio.onloadedmetadata = () => {
        URL.revokeObjectURL(url);
        resolve(audio.duration);
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(0);
      }
    });
  };

  // Calculate total duration whenever files change
  useEffect(() => {
    const calculateTotalDuration = async () => {
      let duration = 0;
      for (let i = 0; i < files.length; i++) {
        duration += await getAudioDuration(files[i].file);
      }
      setTotalDuration(duration);
    };
    calculateTotalDuration();
  }, [files]);

  // Auto-Generate Canvas Cover Art
  useEffect(() => {
    const generateCoverBlob = async () => {
      return new Promise((resolve) => {
        const canvas = document.createElement('canvas');
        canvas.width = 600;
        canvas.height = 600;
        const ctx = canvas.getContext('2d');

        // Backgrounds
        ctx.fillStyle = "#282828";
        ctx.fillRect(0, 0, 600, 600);
        ctx.fillStyle = "#F97300";
        ctx.fillRect(0, 0, 600, 15);

        // Text Properties
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "bold 36px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        const title = meta.title.trim() || "Unknown Audiobook";
        const words = title.split(' ');
        let line = '';
        const lines = [];
        
        for(let n = 0; n < words.length; n++) {
          let testLine = line + words[n] + ' ';
          let metrics = ctx.measureText(testLine);
          if (metrics.width > 500 && n > 0) {
            lines.push(line);
            line = words[n] + ' ';
          } else {
            line = testLine;
          }
        }
        lines.push(line);

        let startY = 300 - ((lines.length - 1) * 20);
        for(let i = 0; i < lines.length; i++){
          ctx.fillText(lines[i], 300, startY + (i * 40));
        }

        canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.9);
      });
    };

    const updateCover = async () => {
      const blob = await generateCoverBlob();
      setGeneratedCoverBlob(blob);
      if (!cover) {
        setCoverPreview(URL.createObjectURL(blob));
      }
    };
    updateCover();
  }, [meta.title, cover]);

  // Handlers for Global Drag & Drop (Folders and Files)
  const handleGlobalDrop = async (e) => {
    e.preventDefault();
    setIsDraggingOverApp(false);
    
    if (status !== 'idle') return;

    const items = e.dataTransfer.items;
    let audioQueue = [];
    let foundCover = null;
    let folderName = null;

    const traverseFileTree = async (item, path = '') => {
      if (item.isFile) {
        const file = await new Promise((resolve) => item.file(resolve));
        if (file.name.match(/\.(mp3|m4a|aac|flac|wav|wma|ogg)$/i)) {
          audioQueue.push(file);
        } else if (!foundCover && file.name.match(/\.(jpg|jpeg|png)$/i)) {
          foundCover = file;
        }
      } else if (item.isDirectory) {
        if (!folderName) folderName = item.name;
        const dirReader = item.createReader();
        const entries = await new Promise((resolve) => dirReader.readEntries(resolve));
        for (let entry of entries) {
          await traverseFileTree(entry, path + item.name + "/");
        }
      }
    };

    for (let i = 0; i < items.length; i++) {
      const item = items[i].webkitGetAsEntry();
      if (item) await traverseFileTree(item);
    }

    if (audioQueue.length > 0) {
      audioQueue.sort((a, b) => a.name.localeCompare(b.name));
      const newFiles = audioQueue.map(f => ({
        id: Math.random().toString(36).substr(2, 9),
        file: f,
        name: f.name,
        customChapterName: f.name.replace(/\.[^/.]+$/, "")
      }));
      setFiles(prev => [...prev, ...newFiles]);
      
      if (!meta.title && folderName) {
        setMeta(prev => ({ ...prev, title: folderName }));
      }
    }

    if (foundCover && !cover) {
      setCover(foundCover);
      setCoverPreview(URL.createObjectURL(foundCover));
    }
  };

  const handleFileImport = (e) => {
    const importedFiles = Array.from(e.target.files).filter(f => 
      f.name.match(/\.(mp3|m4a|aac|flac|wav|wma|ogg)$/i)
    );
    const newFiles = importedFiles.map(f => ({
      id: Math.random().toString(36).substr(2, 9),
      file: f,
      name: f.name,
      customChapterName: f.name.replace(/\.[^/.]+$/, "")
    }));
    setFiles(prev => [...prev, ...newFiles]);
  };

  const handleCoverImport = (e) => {
    const file = e.target.files[0];
    if (file && file.type.startsWith('image/')) {
      setCover(file);
      setCoverPreview(URL.createObjectURL(file));
    }
  };

  // Drag & Drop Reordering Logic
  const handleSort = () => {
    if (dragItem.current !== null && dragOverItem.current !== null && dragItem.current !== dragOverItem.current) {
      let _files = [...files];
      const draggedItemContent = _files.splice(dragItem.current, 1)[0];
      _files.splice(dragOverItem.current, 0, draggedItemContent);
      setFiles(_files);
    }
    dragItem.current = null;
    dragOverItem.current = null;
    setDragOverIndex(null);
  };

  // Encoding Process
  const executeMerge = async () => {
    if (!ffmpegLoaded || !ffmpegRef.current) {
      alert("FFmpeg is still loading. Please wait a moment.");
      return;
    }
    if (files.length === 0) return;

    setStatus('encoding');
    setProgress(0);
    setEta('Scanning durations...');

    const ffmpeg = ffmpegRef.current;
    let totalDurationSec = 0;
    let metadataText = `;FFMETADATA1\n`;
    
    if (meta.title) metadataText += `title=${meta.title}\n`;
    if (meta.author) metadataText += `artist=${meta.author}\n`;
    if (meta.narrator) metadataText += `composer=${meta.narrator}\n`;
    if (meta.genre) metadataText += `genre=${meta.genre}\n`;
    if (meta.year) metadataText += `date=${meta.year}\n`;

    let current_time_ms = 0;
    let inputsList = '';

    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const durationSec = await getAudioDuration(f.file);
      totalDurationSec += durationSec;
      const durationMs = Math.floor(durationSec * 1000);

      const safeName = `input_${i}.audio`;
      await ffmpeg.writeFile(safeName, await fetchFileRef.current(f.file));
      inputsList += `file '${safeName}'\n`;

      if (chapterMode !== 'none' && durationMs > 0) {
        const chapterTitle = chapterMode === 'custom' ? f.customChapterName : f.name.replace(/\.[^/.]+$/, "");
        metadataText += `\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=${current_time_ms}\nEND=${current_time_ms + durationMs}\ntitle=${chapterTitle}\n`;
      }
      current_time_ms += durationMs;
    }

    totalAudioDurationRef.current = totalDurationSec;

    await ffmpeg.writeFile('inputs.txt', inputsList);
    await ffmpeg.writeFile('metadata.txt', metadataText);

    const cmd = ['-y', '-f', 'concat', '-safe', '0', '-i', 'inputs.txt'];
    const hasCover = cover || generatedCoverBlob;
    
    if (cover) {
      await ffmpeg.writeFile('cover.jpg', await fetchFileRef.current(cover));
      cmd.push('-i', 'cover.jpg');
    } else if (generatedCoverBlob) {
      await ffmpeg.writeFile('cover.jpg', await fetchFileRef.current(generatedCoverBlob));
      cmd.push('-i', 'cover.jpg');
    }

    cmd.push('-i', 'metadata.txt', '-map', '0:a');
    
    if (hasCover) {
      cmd.push('-map', '1:v', '-c:v', 'copy', '-disposition:v', 'attached_pic');
    }
    
    cmd.push('-map_metadata', hasCover ? '2' : '1', '-c:a', 'aac', '-b:a', quality, 'output.m4b');

    setEta('Merging files... (This may take a while depending on RAM)');
    
    try {
      await ffmpeg.exec(cmd);
      setProgress(100);
      setEta('Complete!');

      const data = await ffmpeg.readFile('output.m4b');
      const blob = new Blob([data.buffer], { type: 'audio/mp4' });
      const url = URL.createObjectURL(blob);
      
      const a = document.createElement('a');
      a.href = url;
      a.download = `${meta.title ? meta.title.replace(/\s+/g, '_') : 'Audiobook'}.m4b`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      
    } catch (e) {
      console.error(e);
      setEta('Error occurred during encoding.');
    } finally {
      setStatus('idle');
    }
  };

  // Preview Process
  const togglePreview = async () => {
    if (previewPlaying && audioRef.current) {
      audioRef.current.pause();
      setPreviewPlaying(false);
      return;
    }

    if (files.length === 0) return alert("Add an audio file to preview.");
    if (!ffmpegLoaded || !ffmpegRef.current) return alert("FFmpeg not loaded.");

    setStatus('previewing');
    const ffmpeg = ffmpegRef.current;
    const testFile = files[0];

    try {
      await ffmpeg.writeFile('preview_in.audio', await fetchFileRef.current(testFile.file));
      await ffmpeg.exec(['-y', '-i', 'preview_in.audio', '-t', '15', '-vn', '-c:a', 'aac', '-b:a', quality, 'preview_out.m4a']);
      
      const data = await ffmpeg.readFile('preview_out.m4a');
      const blob = new Blob([data.buffer], { type: 'audio/mp4' });
      const url = URL.createObjectURL(blob);
      
      if (audioRef.current) {
        audioRef.current.src = url;
        audioRef.current.play();
        setPreviewPlaying(true);
      }
    } catch (e) {
      console.error(e);
      alert("Failed to render preview.");
    } finally {
      setStatus('idle');
    }
  };

  return (
    <div 
      className="min-h-screen bg-[#121212] text-[#B3B3B3] p-8 font-sans relative"
      onDragOver={(e) => { e.preventDefault(); setIsDraggingOverApp(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setIsDraggingOverApp(false); }}
      onDrop={handleGlobalDrop}
    >
      
      {/* Full-screen Drop Zone Overlay */}
      {isDraggingOverApp && status === 'idle' && (
        <div className="absolute inset-0 z-50 bg-[#F97300]/20 border-4 border-dashed border-[#F97300] m-4 rounded-xl flex items-center justify-center pointer-events-none">
          <div className="bg-[#181818] px-8 py-6 rounded-lg flex flex-col items-center shadow-2xl">
            <UploadCloud className="w-16 h-16 text-[#F97300] mb-4" />
            <h2 className="text-2xl font-bold text-white">Drop Folders or Audio Files</h2>
            <p className="text-sm mt-2 text-[#B3B3B3]">Supported: .mp3, .m4a, .flac, .wav, .jpg covers</p>
          </div>
        </div>
      )}

      {/* Hidden Audio Element for Previews */}
      <audio ref={audioRef} onEnded={() => setPreviewPlaying(false)} className="hidden" />

      {/* Main App Layout */}
      <div className="max-w-6xl mx-auto flex flex-col md:flex-row gap-8">
        
        {/* Left Panel - Core Settings & Files */}
        <div className="flex-1 flex flex-col gap-6">
          
          {/* Title */}
          <div>
            <label className="block text-xs font-bold tracking-widest mb-1 text-[#B3B3B3]">AUDIOBOOK TITLE</label>
            <input 
              type="text" 
              value={meta.title}
              onChange={(e) => setMeta({...meta, title: e.target.value})}
              className="w-full bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:outline-none focus:border-[#F97300] transition" 
              placeholder="Book Title..."
            />
          </div>

          {/* Additional Metadata Accordion */}
          <div>
            <button 
              onClick={() => setShowMeta(!showMeta)} 
              className="text-[#F97300] hover:text-[#FF8C3A] hover:underline text-xs flex items-center gap-1"
            >
              {showMeta ? '▲ Hide Additional Information' : '▼ Additional Information'}
            </button>
            
            {showMeta && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <input type="text" placeholder="Author..." value={meta.author} onChange={e => setMeta({...meta, author: e.target.value})} className="bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:border-[#F97300] outline-none text-sm" />
                <input type="text" placeholder="Narrator..." value={meta.narrator} onChange={e => setMeta({...meta, narrator: e.target.value})} className="bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:border-[#F97300] outline-none text-sm" />
                <input type="text" placeholder="Genre..." value={meta.genre} onChange={e => setMeta({...meta, genre: e.target.value})} className="bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:border-[#F97300] outline-none text-sm" />
                <input type="text" placeholder="Year..." value={meta.year} onChange={e => setMeta({...meta, year: e.target.value})} className="bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:border-[#F97300] outline-none text-sm" />
              </div>
            )}
          </div>

          {/* Quality */}
          <div>
            <label className="block text-xs font-bold tracking-widest mb-1 text-[#B3B3B3]">AAC ENCODING QUALITY</label>
            <select 
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="w-full bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:outline-none focus:border-[#F97300] appearance-none"
            >
              <option value="320k">Maximum Quality AAC (320k - Recommended)</option>
              <option value="256k">High Quality AAC (256k)</option>
              <option value="192k">Standard AAC (192k)</option>
              <option value="128k">Space Saver AAC (128k)</option>
            </select>
          </div>

          {/* File List */}
          <div className="flex flex-col flex-1 min-h-[300px]">
            <div className="flex justify-between items-end mb-1">
              <label className="text-xs font-bold tracking-widest text-[#B3B3B3]">SUPPORTED AUDIO FILES</label>
            </div>
            
            <div className="bg-[#282828] border border-[#3E3E3E] rounded p-2 flex-1 overflow-y-auto relative">
              {files.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-[#888888] pointer-events-none">
                  <p>Drop folders or files here to begin.</p>
                </div>
              ) : (
                <div className="space-y-1">
                  {files.map((file, index) => (
                    <div 
                      key={file.id}
                      draggable
                      onDragStart={(e) => dragItem.current = index}
                      onDragEnter={(e) => {
                        setDragOverIndex(index);
                        dragOverItem.current = index;
                      }}
                      onDragEnd={handleSort}
                      onDragOver={(e) => e.preventDefault()}
                      className={`bg-[#181818] border border-[#3E3E3E] p-2 rounded flex items-center cursor-move hover:border-[#888] transition group
                        ${dragOverIndex === index ? 'border-t-2 border-t-[#F97300]' : ''}`}
                    >
                      <GripVertical className="w-4 h-4 text-[#555] mr-2 group-hover:text-[#F97300]" />
                      <span className="text-sm text-white truncate flex-1">{file.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex justify-between items-center mt-3">
              <button 
                onClick={togglePreview}
                disabled={files.length === 0 || status === 'encoding' || !ffmpegLoaded}
                className="text-sm bg-transparent border border-[#B3B3B3] text-white px-3 py-1.5 rounded hover:border-white transition flex items-center gap-2 disabled:opacity-50"
              >
                {previewPlaying ? <Square className="w-4 h-4 text-[#F97300]" /> : <Play className="w-4 h-4" />}
                {previewPlaying ? 'Stop Preview' : status === 'previewing' ? 'Rendering...' : 'Preview Track'}
              </button>
              
              <div className="flex items-center gap-4">
                <span className="text-sm">{files.length} files loaded</span>
                <label className="cursor-pointer bg-transparent border border-[#B3B3B3] text-white px-4 py-1.5 rounded hover:border-white transition">
                  Import Files
                  <input type="file" multiple accept="audio/*" onChange={handleFileImport} className="hidden" />
                </label>
              </div>
            </div>
          </div>

          {/* Encode Button & Progress */}
          <div className="mt-4">
            <button 
              onClick={executeMerge}
              disabled={files.length === 0 || status !== 'idle' || !ffmpegLoaded}
              className={`w-full ${engineError ? 'bg-red-900 text-red-200' : 'bg-[#F97300] hover:bg-[#FF8C3A]'} disabled:bg-[#3E3E3E] disabled:text-[#888] text-white font-bold py-3 rounded-full transition text-lg flex justify-center items-center gap-2`}
            >
              {status === 'encoding' ? 'ENCODING...' : engineError ? 'ENGINE ERROR (SEE BOTTOM)' : !ffmpegLoaded ? 'LOADING ENGINE...' : <><Download className="w-5 h-5"/> ENCODE TO M4B</>}
            </button>
            
            {status === 'encoding' && (
              <div className="mt-3 space-y-1">
                <div className="h-3 w-full bg-[#282828] border border-[#3E3E3E] rounded-full overflow-hidden">
                  <div className="h-full bg-[#F97300] transition-all duration-300" style={{ width: `${progress}%` }}></div>
                </div>
                <div className="text-center text-xs font-bold text-[#F97300]">{eta}</div>
              </div>
            )}
          </div>

        </div>

        {/* Right Panel - Chapters & Cover */}
        <div className="w-full md:w-[350px] flex flex-col">
          
          {/* Chapter Markers */}
          <div className="mb-8">
            <label className="block text-xs font-bold tracking-widest mb-1 text-[#B3B3B3]">CHAPTER MARKERS</label>
            <select 
              value={chapterMode}
              onChange={(e) => setChapterMode(e.target.value)}
              className="w-full bg-[#282828] text-white border border-[#3E3E3E] rounded p-2 focus:outline-none focus:border-[#F97300] appearance-none mb-2"
            >
              <option value="auto">Auto-Generate from Files</option>
              <option value="custom">Custom Chapter Names</option>
              <option value="none">No Chapters</option>
            </select>
            
            {chapterMode === 'custom' && (
              <button 
                onClick={() => setShowChapterEditor(!showChapterEditor)}
                className="w-full bg-transparent border border-[#B3B3B3] text-white px-3 py-1.5 rounded hover:border-white transition text-sm flex items-center justify-center gap-2"
              >
                <Settings className="w-4 h-4" /> Edit Chapter Names
              </button>
            )}

            {/* Inline Custom Chapter Editor */}
            {chapterMode === 'custom' && showChapterEditor && files.length > 0 && (
              <div className="mt-2 bg-[#181818] border border-[#3E3E3E] rounded p-2 max-h-48 overflow-y-auto space-y-2">
                {files.map((file, idx) => (
                  <div key={file.id} className="flex flex-col gap-1">
                    <span className="text-xs text-[#888] truncate">{file.name}</span>
                    <input 
                      type="text" 
                      value={file.customChapterName}
                      onChange={(e) => {
                        const newFiles = [...files];
                        newFiles[idx].customChapterName = e.target.value;
                        setFiles(newFiles);
                      }}
                      className="bg-[#282828] text-white border border-[#3E3E3E] rounded p-1 text-sm focus:border-[#F97300] outline-none"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex-1"></div> {/* Spacer to push cover to bottom */}

          {/* Cover Art */}
          <div className="flex flex-col items-center">
            <div className="w-[350px] h-[350px] bg-[#181818] rounded-lg overflow-hidden flex items-center justify-center relative border border-[#3E3E3E]">
              {coverPreview ? (
                <img src={coverPreview} alt="Cover" className="w-full h-full object-cover" />
              ) : (
                <div className="text-center text-[#535353] flex flex-col items-center">
                  <ImageIcon className="w-12 h-12 mb-2 opacity-50" />
                  <span className="text-lg">No Cover Art</span>
                </div>
              )}
            </div>
            
            <div className="mt-4 flex gap-3">
              {cover && (
                <button 
                  onClick={() => { setCover(null); setCoverPreview(null); }}
                  className="bg-transparent border border-red-500/50 text-red-400 px-4 py-2 rounded hover:bg-red-500/10 transition text-sm"
                >
                  Remove
                </button>
              )}
              <label className="cursor-pointer bg-transparent border border-[#B3B3B3] text-white px-6 py-2 rounded hover:border-white transition text-sm">
                Change Cover Art
                <input type="file" accept="image/*" onChange={handleCoverImport} className="hidden" />
              </label>
            </div>
          </div>

        </div>
      </div>

      {/* Warnings & Notices (Moved to Bottom) */}
      <div className="max-w-6xl mx-auto mt-8 space-y-4">
        
        {/* Engine Error Warning (Visible if FFmpeg fails to load) */}
        {engineError && (
          <div className="bg-red-900/30 border border-red-500/50 p-4 rounded shadow-md flex items-start gap-4">
            <AlertTriangle className="text-red-400 w-6 h-6 flex-shrink-0 mt-1" />
            <div>
              <h3 className="text-red-100 font-bold mb-1">Failed to Load Audio Engine</h3>
              <p className="text-sm text-red-200">
                {engineError} Please check your internet connection or browser ad-blockers, as they might be preventing the WebAssembly engine from downloading.
              </p>
            </div>
          </div>
        )}

        {/* Memory Limit Warning - Only shows if over 3 hours (10800 seconds) */}
        {totalDuration > 10800 && (
          <div className="bg-[#282828] border-l-4 border-[#F97300] p-4 rounded-r shadow-md flex items-start gap-4">
            <AlertTriangle className="text-[#F97300] w-6 h-6 flex-shrink-0 mt-1" />
            <div>
              <h3 className="text-white font-bold mb-1">Large Audiobook Detected (Browser Memory Limits Apply)</h3>
              <p className="text-sm">
                This web version processes everything directly in your browser's RAM. Because your audiobook is over 3 hours long, your browser might crash during encoding due to memory limits.
                <strong> For large audiobooks, please download the multi-threaded Desktop version.</strong>
              </p>
              <div className="mt-3 flex gap-3">
                <a href="https://github.com/nawnawnawnawnaw55-jpg/M4B-Audiobook-Encoder/releases/tag/V1.0.0" target="_blank" rel="noreferrer" className="text-[#F97300] hover:underline text-sm font-semibold">View Desktop Release Notes</a>
                <a href="https://github.com/nawnawnawnawnaw55-jpg/M4B-Audiobook-Encoder/releases/download/V1.0.0/M4B-Audiobook-Encoder.exe" className="bg-[#F97300] text-white px-3 py-1 rounded text-sm hover:bg-[#FF8C3A] transition">Download .EXE</a>
              </div>
            </div>
          </div>
        )}

        {/* COOP/COEP Dev Warning */}
        {!isCoiIsolated && (
          <div className="bg-red-900/30 border border-red-500/50 p-4 rounded shadow-md flex items-start gap-4">
            <Info className="text-red-400 w-6 h-6 flex-shrink-0 mt-1" />
            <div>
              <h3 className="text-red-100 font-bold mb-1">Notice: Running in Single-Threaded Mode</h3>
              <p className="text-sm text-red-200">
                Because GitHub Pages limits custom HTTP headers, the encoder has automatically fallen back to the single-threaded engine. It will still work perfectly, but encoding may be slightly slower!
              </p>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}