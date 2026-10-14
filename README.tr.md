# Auto Video Studio

Türkçe · **[English](README.md)**

Bir kanal fikrini tamamen otomatik çalışan bir YouTube kanalına çevirir: senaryo → seslendirme → görseller →
bitmiş video ve Shorts → SEO metinleri ve kapak → zamanlanmış yükleme. Kurulumda birkaç soruyu bir kez
cevaplarsınız; sonra yeni bölümler sizin takviminize göre üretilir. İsterseniz onay adımlarıyla kontrol sizde kalır.

Gerçek bir çocuk animasyon kanalının üretim hattından çıkarıldı ve her formata uyacak şekilde genelleştirildi:
çocuk hikayeleri, eğitici anlatım, ilginç bilgiler / liste videoları, hikaye anlatımı, uyku masalları ya da
kendi formatınız.

## Neler yapar

| Adım | Ne olur | Seçenekler |
| --- | --- | --- |
| Kurulum sihirbazı | Kanalınızı sorar; `kanal/kanal.yaml` ve size özel kanal kimliği `kanal/KANAL.md` dosyasını yazar | Konsepti yapay zeka detaylandırabilir: karakterler, görsel stil, anahtar kelimeler, ilk bölüm fikirleri |
| Senaryo | Sahneler, çekimler, her dilde replikler, resim tarifleri ve Shorts seçimi | **Her şeyi yapay zeka yazar** · **siz fikir verirsiniz, yapay zeka yazar** · **siz yazarsınız, yapay zeka çevirir/storyboard yapar** |
| Ses | Her replik ve dil için ayrı ses; temizlenir, ses düzeyi eşitlenir, önbelleğe alınır | **ElevenLabs** · **Google Cloud TTS (Chirp 3 HD)** · **Gemini TTS** · ücretsiz test sesi |
| Görüntü | Her çekime (ya da sahneye) bir resim + yavaş kamera hareketi | **Yapay zeka resmi** (Gemini / OpenAI; tutarlılık için karakter referans resmi) · **kukla** (PNG katmanlar + dudak senkronu) · **kendi resimleriniz** · test kartları · **eklenti** |
| Video | 1080p, sahne geçişleri, açılış kartı, isteğe bağlı gömülü altyazı, sesi kısan fon müziği, −14 LUFS ses düzeyi | çok dil: **tek videoda ek ses kanalları** ya da **her dile ayrı video** |
| Shorts | Bölümden 9:16 kesitler, kamera konuşanı takip eder, altyazılı | dil başına |
| SEO | Başlık, açıklama (ilk 150 karakterde kanca, bölüm zamanları), etiketler (≤ 500 karakter), hashtag, kapak — her dil için | yapay zeka yazar, kod YouTube sınırlarına göre denetler |
| Yükleme | Video + dillere göre başlık/açıklama, altyazılar, kapak, oynatma listesi, çocuklara özel ve yapay zeka bildirimi | zamanlanmış / özel / liste dışı / herkese açık |
| Zamanlama | Seçtiğiniz gün ve saatlerde üretir, yayın saatlerinize yerleştirir | Windows Görev Zamanlayıcı, cron ya da açık kalan döngü |

## Gereksinimler

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) (Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; Linux: `apt install ffmpeg`)
- İsteğe bağlı: kukla modunda daha doğru ağız hareketi için [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync/releases) (PATH'te)
- Yalnız seçtiğiniz servislerin API anahtarları (ücretsiz test modunda hiçbiri gerekmez)

```bash
git clone https://github.com/<siz>/auto-video-studio.git
cd auto-video-studio
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python studio.py kurulum        # kurulum sihirbazı
python studio.py doktor         # araçları, anahtarları, sesleri kontrol eder
python studio.py yeni           # ilk bölümü üretir
```

Önce ücretsiz deneyin: `python studio.py kurulum --cevaplar ornekler/cevaplar-ornek.yaml`, sonra
`ornekler/senaryo-ornegi.tr.md` dosyasını `kanal/icerik/senaryolar/` içine kopyalayıp `python studio.py yeni`.
Test sesleriyle iki dilli kısa bir kukla bölümü, altyazılar, bir Short, SEO metni ve kapak çıkar.

## Komutlar

| Komut | Ne yapar |
| --- | --- |
| `kurulum` | Kurulum sihirbazı (`--cevaplar dosya.yaml` ile sorusuz) |
| `doktor` | ffmpeg, anahtarlar, ses kimlikleri, referans resimleri, YouTube girişi |
| `yeni` | Sıradaki bölümü üretir (`--fikir "…"` ya da `--dosya senaryo.md` ile sırayı atlar) |
| `durum` | Bütün bölümler, aşamaları ve YouTube bağlantıları |
| `onayla <id>` | Onay adımını geçer (senaryo ya da yükleme) ve devam eder |
| `duzelt <id> "geri bildirim"` | Yapay zeka senaryoyu geri bildiriminize göre yeniden yazar |
| `uret [id]` | Hata sonrası bölümü yeniden dener / devam ettirir |
| `tick` | Zamanı gelen işi yapar (cron / Görev Zamanlayıcı 15 dakikada bir çağırır) |
| `zamanlayici` | Açık kalır, her dakika kontrol eder; `--kur` işletim sistemi görevini kurar |
| `youtube-baglan` | Yükleme için bir kerelik Google girişi |
| `referans` | Karakter referans resimlerini çizer (yapay zeka resimlerinde tutarlılık) |
| `kukla-ornek` | Basit örnek kukla paketleri yazar |
| `sesler` | ElevenLabs seslerinizi kimlikleriyle listeler |

## Bir bölümün yolculuğu

```
senaryo → [senaryo onayı] → görseller → ses + render → SEO + kapak → [yükleme onayı] → yükleme → bitti
```

Köşeli parantezdeki onaylar varsayılan olarak açıktır. Senaryo onayında `cikti/<id>/senaryo.md` dosyasını açın;
doğrudan düzenleyin ya da `duzelt` çalıştırın, sonra `onayla`. Bir bölümün her şeyi `cikti/<id>/` içindedir:
`senaryo.json/.md`, `gorseller/`, `video.mp4`, `ses.<dil>.m4a`, `altyazi.<dil>.srt`, `shorts/`, `seo.json`,
`yayin.md` (kopyala-yapıştır sayfası), `kapak.<dil>.jpg`, `durum.json` (aşama, maliyet, YouTube kimlikleri).

## Senaryo modları

- **ai** — yapay zeka yeni bir konu seçer (önceki bölümleri görür, tekrar etmez) ve yazar.
- **idea (fikir)** — `kanal/icerik/fikirler.md` içine her satıra bir fikir yazın; en üstteki kullanılır ve "Kullanılanlar"a taşınır.
- **manual (elle)** — senaryoları basit Markdown biçiminde (`ornekler/senaryo-ornegi.*.md`) `kanal/icerik/senaryolar/`
  klasörüne yazın. Yapay zeka tanımlıysa eksik çeviriler, resim tarifleri ve Shorts doldurulur; sizin metniniz kelimesi kelimesine kalır.

Yapay zeka her seferinde kanal kimliğiniz `kanal/KANAL.md` dosyasını okur. Ton, kurallar ve karakterler için onu düzenleyin.

## Görüntü modları

- **ai_images** — her çekime (maliyet için isterseniz her sahneye) bir resim. Her karakterin referans resmini
  `kanal/karakterler/<id>/referans.png` olarak koyun (`python studio.py referans` çizer); karakterin göründüğü her
  resimle birlikte gönderilir, karakter tutarlı kalır.
- **puppet (kukla)** — kendi kesme karakterleriniz arka planların üzerinde canlanır: ağız sese uyar, gözler kırpılır,
  gövde nefes alır, konuşan dinleyene döner. Paket biçimi (hepsi aynı tuval boyutunda, şeffaf, sağa bakan):
  `kanal/karakterler/<id>/kukla/govde.png`, `agiz_X.png … agiz_H.png` (en az kapalı `agiz_X` + açık `agiz_D`),
  isteğe bağlı `goz_kapali.png`, `kukla.json` `{"height": 0.42}`.
- **local_images** — çekim ya da sahne kimliğiyle adlandırılmış resimleri (`S1_01.png`, `S2.jpg`) `cikti/<id>/gorseller/` içine koyun.
- **plugin (eklenti)** — kendi çizim motorunuz: `render_picture(cfg, script, timeline, images_dir, out, size)`
  fonksiyonu olan ve sessiz video yazan bir Python dosyası; ses, altyazı, SEO ve yükleme otomatik kalır.

## Sesler

Her karakterin ve anlatıcının sesini `kanal/kanal.yaml` içinde (`voice.id`) belirleyin. ElevenLabs: kütüphanenizdeki
herhangi bir voice id (`python studio.py sesler`). Google / Gemini: `Leda`, `Puck`, `Kore` gibi ses adı. Senaryodaki
duygu notları, ifadeli modellerde oyunculuk yönlendirmesine dönüşür.

Çok dilli kanallarda iki dil aynı zaman çizelgesini paylaşır: bir dil çok uzun kalırsa, model hız ayarını
destekliyorsa o dil daha hızlı yeniden okunur (destekleyip desteklemediği otomatik öğrenilir; bazı modeller yok
sayıyor), sonra en uzun ses en fazla %8 hızlandırılır, kısa olan en fazla %12 yavaşlatılır (perde değişmeden).
En iyi senkron için replikleri dillerde benzer uzunlukta yazın/yazdırın. İsteğe bağlı olarak her replik yazıya
döktürülüp eksik kelime varsa yeniden üretilebilir (`voice.transcript_check`, Gemini anahtarı gerekir); döküm
alınamazsa replik kabul edilir, boşuna yeniden faturalanmaz.

## YouTube bağlantısı

1. [Google Cloud Console](https://console.cloud.google.com/) → yeni proje → **YouTube Data API v3**'ü etkinleştirin.
2. OAuth izin ekranı (External, kendinizi test kullanıcısı ekleyin) → Kimlik bilgileri → **OAuth istemci kimliği → Masaüstü uygulaması**.
3. JSON'u `.gizli/youtube_client_secret.json` olarak kaydedin, sonra `python studio.py youtube-baglan`.

Otomatikleştirmeden önce sınırları bilin:
- **Denetim:** doğrulanmamış bir projeden API ile yüklenen videolar, proje YouTube'un
  [API uyum denetiminden](https://support.google.com/youtube/contact/yt_api_form) geçene kadar **özel** kalır.
  O zamana kadar `privacy: private` kullanıp YouTube Studio'dan yayınlayın.
- **Kota:** günde varsayılan 10.000 birim. Bir yükleme 1.600, her altyazı 400, kapak ve oynatma listesi 50'şer →
  günde yaklaşık bir bölüm + iki Shorts.
- **Ek ses kanalları** (çok dilli ses) API ile eklenemez: `ses.<dil>.m4a` dosyalarını YouTube Studio → Diller'den
  ekleyin. Ya da `multi_language: separate_videos` seçin.
- **Kapak** yüklemek için kanalın telefonla doğrulanmış olması gerekir.

## Zamanlama

```yaml
schedule:
  timezone: Europe/Istanbul
  produce: [{days: [mon, thu], time: "09:00"}]   # pzt/per de yazılabilir
  publish_times: ["17:00"]
  publish_days: [tue, fri]       # boş = her gün
```

`python studio.py zamanlayici --kur` Windows Görev Zamanlayıcı'ya görev ekler ya da cron satırını yazar; ikisi de
15 dakikada bir `studio.py tick` çalıştırır. Bilgisayar kapalıyken kaçan üretim 12 saat içinde açılınca yapılır.
Onaylanan bölümler sonraki kontrolde devam eder. Bilgisayarınız kapalıyken de çalışsın isterseniz `tick` komutunu
küçük bir sunucuda ya da zamanlanmış GitHub Actions işinde çalıştırın (anahtarları depo gizli değişkenlerinde,
`kanal/` klasörünü özel bir depoda tutun).

## Maliyet (kabaca)

| Kalem | Tipik |
| --- | --- |
| Senaryo + SEO (Claude / Gemini) | bölüm başına birkaç sent ile ~0,30 $ |
| ElevenLabs | 1.000 karakter ~0,08 $ (v3/v4); iki dilde 10 dk ≈ 25 bin karakter |
| Google Chirp 3 HD | ayda 1 milyon karakter ücretsiz, sonra karakter başına |
| Yapay zeka resimleri | tanesi ~0,02–0,04 $; 10 dk ≈ 40–60 çekim (`per: scene` ile azaltın) |

Her bölüm ne harcadığını `durum.json` → `costs` içine yazar.

## YouTube kuralları

Çocuklara özel ayarı her yüklemede bildirilir (yorumlar ve kişiselleştirilmiş reklamlar kapanır). Gerçekçi yapay
zeka içeriği bildirilmelidir; yükleme bu işareti varsayılan olarak açar. YouTube seri üretilmiş, birbirini tekrar
eden içeriğe para ödemez: her bölümü özgün tutun, süreçte bir insan bulunsun (onay adımları bunun için var) ve makul
bir tempoda yayınlayın. Yayınladığınız içerikten ve müzik/ses lisanslarından siz sorumlusunuz.

## Gizlilik

API anahtarları yalnız `.env` dosyasında, YouTube girişi `.gizli/` klasöründe durur. İkisi de, kişisel kanal
verileriniz (`kanal/`) ve çıktılarınız (`cikti/`) ile birlikte `.gitignore` içindedir. Anahtarları asla bir
issue'ya ya da commit'e yapıştırmayın.

## Lisans

MIT
