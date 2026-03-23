# WB Monitor Frontend

Modern Next.js + TypeScript + Tailwind CSS + Recharts dashboard for monitoring Wildberries products.

## Tech Stack

- **Framework**: Next.js 16 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS v4
- **Charts**: Recharts
- **Utilities**: clsx, tailwind-merge

## Getting Started

### Prerequisites

- Node.js 18+ 
- npm or yarn

### Installation

1. Install dependencies:
```bash
npm install
```

2. Create environment file:
```bash
cp .env.example .env.local
```

3. Update `.env.local` with your backend URL:
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Development

Run the development server:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Project Structure

```
src/
├── app/                    # Next.js App Router
│   ├── layout.tsx          # Root layout with Telegram Web App SDK
│   ├── page.tsx            # Main dashboard page
│   └── globals.css         # Global styles
├── components/             # React components
│   ├── TrackCard.tsx       # Product card with price chart
│   ├── StatsCard.tsx       # Statistics card
│   ├── LoadingSpinner.tsx  # Loading states
│   ├── EmptyState.tsx      # Empty state component
│   └── index.ts            # Component exports
├── lib/                    # Utilities
│   ├── api.ts              # API client
│   └── utils.ts            # Helper functions
└── types/                  # TypeScript types
    └── index.ts            # API response types
```

## Features

- 📊 **Price History Charts**: Visualize price changes over time using Recharts
- 📦 **Track Management**: Toggle monitoring on/off for each product
- 📱 **Telegram Integration**: Native Telegram Web App support with theme adaptation
- 🎨 **Modern UI**: Beautiful, responsive design with Tailwind CSS
- 🌙 **Dark Mode**: Automatic dark mode support based on system preferences
- ⚡ **Real-time Updates**: Optimistic UI updates for instant feedback

## API Integration

The frontend communicates with the FastAPI backend using these endpoints:

- `GET /api/tracks` - Fetch dashboard data with price history
- `POST /api/action` - Toggle track monitoring status

Authentication is handled via Telegram Web App `initData` sent in the `X-Telegram-Init-Data` header.

## Building for Production

Create a production build:

```bash
npm run build
```

Start the production server:

```bash
npm start
```

## Deployment

The frontend can be deployed to any platform that supports Next.js:

- Vercel (recommended)
- Netlify
- Railway
- Your own server with PM2/Docker

Make sure to set the `NEXT_PUBLIC_API_URL` environment variable to point to your backend.
