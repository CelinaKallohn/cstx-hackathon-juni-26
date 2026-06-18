import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';
import { CsvDataService } from '../services/csv-data.service';

// Register required ECharts components for line chart
echarts.use([LineChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

interface DayData {
  times: string[];
  profit: number[]; // in €
}

@Component({
  selector: 'app-gewinn',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './gewinn.component.html',
  styleUrls: ['./gewinn.component.css'],
})
export class Gewinn implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  dates: string[] = [];
  selectedDateIndex = 0;

  private dateSub?: Subscription;

  constructor(private readonly dateService: DateSelectionService, private readonly csvService: CsvDataService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Gewinn:', err));
  }

  private async init(): Promise<void> {
    try {
      await this.csvService.ensureLoaded();
      this.dates = this.csvService.getAvailableDates();
      this.selectedDateIndex = Math.max(0, this.dates.length - 1);

      const currentDates = (this.dateService as any)['datesSubject'].getValue() || [];
      const mergedDates = Array.from(new Set([...currentDates, ...this.dates])).sort((a, b) => {
        const pa = a.split('.').map(Number);
        const pb = b.split('.').map(Number);
        const da = new Date(pa[2], pa[1] - 1, pa[0]);
        const db = new Date(pb[2], pb[1] - 1, pb[0]);
        return da.getTime() - db.getTime();
      });
      if (mergedDates.length > 0) {
        this.dateService.setAvailableDates(mergedDates);
      }

      const initDate = this.selectedDate();
      if (initDate) this.updateChartForDate(initDate);

       this.dateSub = this.dateService.date$.subscribe(iso => {
         if (!iso) return;
         const parts = iso.split('-');
         if (parts.length !== 3) return;
         const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
         const idx = this.dates.indexOf(dateStr);
         if (idx !== -1 && idx !== this.selectedDateIndex) {
           this.selectedDateIndex = idx;
           if (dateStr) this.updateChartForDate(dateStr);
         } else if (idx === -1 && !this.dates.includes(dateStr)) {
           // Date not available, show empty chart
           this.updateChartForDate(dateStr);
         }
       });

      window.addEventListener('resize', this.onResize);
    } catch (err) {
      console.error(err);
    }
  }

  public selectedDate(): string | null {
    return this.dates.length > 0 ? this.dates[this.selectedDateIndex] : null;
  }

  // CSV parsing delegated to CsvDataService

     private updateChartForDate(date: string) {
       const d = this.csvService.getGewinnDataByDate(date);
       const simulationData = this.csvService.getProfitSimulationDataByDate(date);
       const predictionData = this.csvService.getProfitPredictionDataByDate(date);

       // Use real data if available, otherwise simulation data
       const hasRealData = d && d.times.length > 0;
       const hasSimulationData = !hasRealData && simulationData && simulationData.profit.length > 0;
       const hasPredictionData = predictionData && predictionData.profit.length > 0;

      if (!hasRealData && !hasSimulationData && !hasPredictionData) {
        // Show empty chart if no data available
        const emptyOption: any = {
          title: { text: `Gewinn` },
          legend: {
            data: ['Gewinn', 'Verlust', 'Vorhersage'],
            top: '4%',
            left: 'center',
          },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: [] },
          yAxis: { type: 'value', name: 'Gewinn (€)' },
          series: [],
          grid: { left: '10%', right: '10%', bottom: '15%' },
        };
        this.chartInstance?.setOption(emptyOption, true);
        return;
      }

      // Use real data, or simulation data if not available
      const dataSource = hasRealData ? d : (hasSimulationData ? { times: simulationData.times, profit: simulationData.profit } : null);

      // Create full 24-hour time axis
      const fullTimes = [];
      for (let h = 0; h < 24; h++) {
        fullTimes.push(`${String(h).padStart(2, '0')}:00:00`);
      }

      // Map data to the full 24-hour axis
      const mapDataToFullAxis = (sourceTimes: string[], sourceData: any[]): any[] => {
        const fullData = new Array(24).fill(null);

        for (let i = 0; i < sourceTimes.length; i++) {
          const time = sourceTimes[i];
          const hour = parseInt(time.split(':')[0]);
          if (hour >= 0 && hour < 24) {
            fullData[hour] = sourceData[i];
          }
        }

        return fullData;
      };

      // Map data to the full axis
      const fullProfit = (hasRealData || hasSimulationData) && dataSource ? mapDataToFullAxis(dataSource.times, dataSource.profit) : new Array(24).fill(null);
      const fullPredictionProfit = hasPredictionData ? mapDataToFullAxis(predictionData.times, predictionData.profit) : new Array(24).fill(null);

      // Calculate min/max including prediction data
      let profitMin = 0;
      let profitMax = 0;

      const numericProfit = fullProfit.filter((v): v is number => v !== null && v !== undefined);
      if (numericProfit.length > 0) {
        profitMin = Math.min(...numericProfit);
        profitMax = Math.max(...numericProfit);
      }

      const numericPredictionProfit = fullPredictionProfit.filter((v): v is number => v !== null && v !== undefined);
      if (numericPredictionProfit.length > 0) {
        profitMin = Math.min(profitMin, Math.min(...numericPredictionProfit));
        profitMax = Math.max(profitMax, Math.max(...numericPredictionProfit));
      }

     const padding = (profitMax - profitMin) * 0.1 || 5;
     const yMin = Math.floor(profitMin - padding);
     const yMax = Math.ceil(profitMax + padding);

     const positiveData = fullProfit.map(v => (v !== null && v !== undefined && v >= 0) ? v : undefined);
     const negativeData = fullProfit.map(v => (v !== null && v !== undefined && v < 0) ? v : undefined);

     const option: any = {
       title: { text: `Gewinn` },
       legend: {
         data: ['Gewinn', 'Verlust', 'Vorhersage'],
         top: '4%',
         left: 'center',
       },
       tooltip: {
         trigger: 'axis',
         formatter: (params: any) => {
           if (!Array.isArray(params)) params = [params];
           let result = params[0].axisValue + '<br/>';
           for (const p of params) {
             const value = p.value;
             if (value !== undefined && value !== null) {
               const label = value >= 0 ? `✓ ${p.seriesName}: €${Math.abs(value).toFixed(2)}` : `✗ ${p.seriesName}: €${Math.abs(value).toFixed(2)}`;
               result += label + '<br/>';
             }
           }
           return result;
         },
       },
       xAxis: {
         type: 'category',
         data: fullTimes,
         boundaryGap: false,
         axisLabel: {
           interval: 2,
           formatter: (value: string) => {
             const parts = value.split(':');
             return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
           },
         },
       },
       yAxis: { type: 'value', name: 'Gewinn (€)', min: yMin, max: yMax },
       series: [],
       grid: { left: '10%', right: '10%', bottom: '15%' },
     };

      // Add real or simulation data if available
      if (hasRealData || hasSimulationData) {
        option.series.push(
          {
            name: 'Gewinn',
            type: 'line',
            data: positiveData,
            smooth: true,
            itemStyle: { color: '#91CB74' },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(145, 203, 116, 0.3)' },
                { offset: 1, color: 'rgba(145, 203, 116, 0.1)' },
              ]),
            },
          },
          {
            name: 'Verlust',
            type: 'line',
            data: negativeData,
            smooth: true,
            itemStyle: { color: '#EE6666' },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(238, 102, 102, 0.3)' },
                { offset: 1, color: 'rgba(238, 102, 102, 0.1)' },
              ]),
            },
          }
        );
      }

      // Add prediction data if available (light blue)
      if (hasPredictionData && fullPredictionProfit.some(v => v !== null && v !== undefined)) {
        option.series.push({
          name: 'Vorhersage',
          type: 'line',
          data: fullPredictionProfit,
          smooth: true,
          lineStyle: { width: 2, color: '#87CEEB' },
          itemStyle: { color: '#87CEEB' },
          showSymbol: false,
        });
      }

      this.chartInstance?.setOption(option, true);
    }

  private readonly onResize = () => {
    if (this.chartInstance) this.chartInstance.resize();
  };

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    if (this.chartInstance) {
      this.chartInstance.dispose();
      this.chartInstance = null;
    }
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}

