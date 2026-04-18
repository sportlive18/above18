export default {
  async fetch(request) {

    const url = new URL(request.url);

    // 🔥 IMAGE PROXY ROUTE
    if (url.pathname === "/img") {
      const target = url.searchParams.get("url");

      if (!target) {
        return new Response("No URL", { status: 400 });
      }

      const img = await fetch(target, {
        headers: {
          "Referer": "https://store.externulls.com/",
          "User-Agent": "Mozilla/5.0"
        }
      });

      return new Response(img.body, {
        headers: {
          "Content-Type": "image/webp",
          "Access-Control-Allow-Origin": "*"
        }
      });
    }

    // 🔥 ORIGINAL API
    const API = "https://store.externulls.com/tag/videos/comatozze?limit=48&offset=0";
    const BASE = "https://video.pat.com/";

    const res = await fetch(API, {
      headers: { "User-Agent": "Mozilla/5.0" }
    });

    const data = await res.json();

    const videos = data.map(item => {

      const title = item?.file?.data?.[0]?.cd_value || "No Title";

      const dur = item?.file?.fl_duration || 0;
      const minutes = Math.floor(dur / 60);
      const seconds = dur % 60;
      const duration = `${minutes}:${seconds.toString().padStart(2, '0')}`;

      const videoId = item?.file?.id;
      const thumbId = item?.fc_facts?.[0]?.fc_thumbs?.[0] || 1;

      // 🔥 ORIGINAL THUMB
      const rawThumb = `https://thumbs.externulls.com/videos/${videoId}/${thumbId}.webp?size=480x270`;

      // 🔥 PROXIED THUMB
      const thumbnail = `${url.origin}/img?url=${encodeURIComponent(rawThumb)}`;

      const hlsPath = item?.file?.hls_resources?.fl_cdn_multi;
      const m3u8 = BASE + hlsPath + ".m3u8";

      return { title, duration, thumbnail, m3u8 };
    });

    return new Response(JSON.stringify({
      status: true,
      data: videos
    }), {
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
      }
    });
  }
};
